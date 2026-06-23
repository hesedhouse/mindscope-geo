"""MindScope GEO — FastAPI 라우터."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import AI_ENGINES, OPENAI_API_KEY
from app.db.database import get_session
from app.db.models import Brand, Client, DiagnosisRequest, ScanPrompt, ScanResult, User, VisibilityScore
from app.auth import get_current_user, require_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ClientCreate(BaseModel):
    name: str
    domain: str | None = None


class BrandCreate(BaseModel):
    client_id: int
    name: str
    competitors: list[str] = []
    keywords: list[str] = []


class PromptCreate(BaseModel):
    prompt_text: str
    category: str = "추천"


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """메인 대시보드 페이지."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dashboard.html")


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """초기 설정 페이지."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "setup.html")


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------

@router.get("/api/clients")
async def list_clients(
    session: AsyncSession = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    query = select(Client).options(selectinload(Client.brands)).order_by(Client.created_at.desc())
    # 토큰이 있으면 해당 유저의 클라이언트만 필터 (+ user_id가 NULL인 것도 포함)
    if current_user:
        query = query.where(
            (Client.user_id == current_user.id) | (Client.user_id.is_(None))
        )
    result = await session.execute(query)
    clients = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "domain": c.domain,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "brands": [{"id": b.id, "name": b.name} for b in c.brands],
        }
        for c in clients
    ]


@router.post("/api/clients")
async def create_client(
    body: ClientCreate,
    session: AsyncSession = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    client = Client(
        name=body.name,
        domain=body.domain,
        user_id=current_user.id if current_user else None,
    )
    session.add(client)
    await session.commit()
    await session.refresh(client)
    return {"id": client.id, "name": client.name, "domain": client.domain}


# ---------------------------------------------------------------------------
# Brand CRUD
# ---------------------------------------------------------------------------

@router.get("/api/brands/{brand_id}")
async def get_brand(brand_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(Brand)
        .where(Brand.id == brand_id)
        .options(selectinload(Brand.scan_prompts), selectinload(Brand.visibility_scores))
    )
    brand = result.scalar_one_or_none()
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다.")

    # 최신 점수
    latest_scores = {}
    for vs in sorted(brand.visibility_scores, key=lambda v: v.calculated_at or datetime.min, reverse=True):
        if vs.engine not in latest_scores:
            latest_scores[vs.engine] = {
                "engine": vs.engine,
                "score": vs.score,
                "share_of_voice": vs.share_of_voice,
                "avg_sentiment": vs.avg_sentiment,
                "total_prompts": vs.total_prompts,
                "mentioned_prompts": vs.mentioned_prompts,
                "calculated_at": vs.calculated_at.isoformat() if vs.calculated_at else None,
            }

    return {
        "id": brand.id,
        "name": brand.name,
        "client_id": brand.client_id,
        "competitors": brand.competitors or [],
        "keywords": brand.keywords or [],
        "prompt_count": len([p for p in brand.scan_prompts if p.is_active]),
        "latest_scores": latest_scores,
    }


@router.post("/api/brands")
async def create_brand(body: BrandCreate, session: AsyncSession = Depends(get_session)):
    client = await session.get(Client, body.client_id)
    if not client:
        raise HTTPException(404, "클라이언트를 찾을 수 없습니다.")
    brand = Brand(
        client_id=body.client_id,
        name=body.name,
        competitors=body.competitors,
        keywords=body.keywords,
    )
    session.add(brand)
    await session.commit()
    await session.refresh(brand)
    return {"id": brand.id, "name": brand.name, "client_id": brand.client_id}


# ---------------------------------------------------------------------------
# Prompt management
# ---------------------------------------------------------------------------

@router.post("/api/brands/{brand_id}/prompts")
async def add_prompt(brand_id: int, body: PromptCreate, session: AsyncSession = Depends(get_session)):
    brand = await session.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다.")
    prompt = ScanPrompt(brand_id=brand_id, prompt_text=body.prompt_text, category=body.category)
    session.add(prompt)
    await session.commit()
    await session.refresh(prompt)
    return {"id": prompt.id, "prompt_text": prompt.prompt_text, "category": prompt.category}


@router.post("/api/brands/{brand_id}/prompts/generate")
async def generate_prompts(brand_id: int, session: AsyncSession = Depends(get_session)):
    """키워드 기반 프롬프트 자동 생성 (OpenAI 사용)."""
    brand = await session.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다.")

    keywords = brand.keywords or []
    if not keywords:
        raise HTTPException(400, "브랜드에 키워드가 설정되어 있지 않습니다.")

    generated: list[dict] = []

    if OPENAI_API_KEY:
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=OPENAI_API_KEY)

            keyword_str = ", ".join(keywords)
            system_msg = (
                "당신은 한국어 검색 프롬프트 생성기입니다. "
                "사용자가 AI 챗봇에 물어볼 법한 자연스러운 한국어 질문을 만들어주세요. "
                "각 질문은 한 줄에 하나씩, 번호 없이 작성하세요."
            )
            user_msg = (
                f"다음 키워드와 관련된 한국어 검색 프롬프트를 8개 생성해주세요.\n"
                f"키워드: {keyword_str}\n"
                f"브랜드: {brand.name}\n\n"
                f"유형: 추천 질문, 비교 질문, 순위 질문, 특정 상황 질문 등 다양하게 섞어서.\n"
                f"예시: '좋은 선크림 추천해줘', '선크림 브랜드 비교해줘', '가성비 선크림 순위'"
            )

            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.9,
                max_tokens=1024,
            )
            lines = (resp.choices[0].message.content or "").strip().split("\n")
            for line in lines:
                text = line.strip().lstrip("0123456789.-) ").strip()
                if text and len(text) > 5:
                    prompt = ScanPrompt(brand_id=brand_id, prompt_text=text, category="자동생성")
                    session.add(prompt)
                    generated.append({"prompt_text": text, "category": "자동생성"})

            await session.commit()
        except Exception as e:
            logger.error("프롬프트 자동 생성 실패: %s", e)
            raise HTTPException(500, f"프롬프트 생성 실패: {str(e)}")
    else:
        # Fallback: 키워드 기반 템플릿
        templates = [
            "좋은 {kw} 추천해줘",
            "{kw} 브랜드 비교해줘",
            "가성비 {kw} 순위",
            "{kw} 어디 제품이 좋아?",
            "인기 있는 {kw} 알려줘",
        ]
        for kw in keywords:
            for tmpl in templates:
                text = tmpl.format(kw=kw)
                prompt = ScanPrompt(brand_id=brand_id, prompt_text=text, category="자동생성")
                session.add(prompt)
                generated.append({"prompt_text": text, "category": "자동생성"})
        await session.commit()

    return {"generated_count": len(generated), "prompts": generated}


# ---------------------------------------------------------------------------
# Scan execution
# ---------------------------------------------------------------------------

@router.post("/api/scan/{brand_id}")
async def run_scan(
    brand_id: int,
    session: AsyncSession = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    """수동 스캔 실행 — 활성 프롬프트를 각 AI 엔진에 전송."""
    # ── 플랜 제한 체크 ──
    if current_user:
        from app.plans import check_daily_scan_limit, check_limit

        user_plan = current_user.plan or "free"

        # 활성 프롬프트 수 체크
        prompt_count_result = await session.execute(
            select(func.count(ScanPrompt.id)).where(
                ScanPrompt.brand_id == brand_id, ScanPrompt.is_active == True
            )
        )
        prompt_count = prompt_count_result.scalar() or 0
        if not check_limit(user_plan, "prompts", prompt_count):
            raise HTTPException(
                403,
                f"현재 플랜({user_plan})의 프롬프트 제한을 초과했습니다. "
                "업그레이드가 필요합니다.",
            )

        # 일일 스캔 횟수 체크
        today = date.today()
        scan_count_result = await session.execute(
            select(func.count(ScanResult.id))
            .join(ScanPrompt)
            .where(
                ScanPrompt.brand_id == brand_id,
                func.date(ScanResult.scanned_at) == today,
            )
        )
        today_scan_count = scan_count_result.scalar() or 0
        # 프롬프트 수 기준 (각 프롬프트 x 엔진이 1 스캔 세트)
        scan_set_count = 1 if today_scan_count > 0 else 0
        if not check_daily_scan_limit(user_plan, scan_set_count):
            raise HTTPException(
                403,
                f"현재 플랜({user_plan})의 일일 스캔 제한을 초과했습니다. "
                "내일 다시 시도하거나 업그레이드하세요.",
            )

    brand = await session.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다.")

    result = await session.execute(
        select(ScanPrompt).where(ScanPrompt.brand_id == brand_id, ScanPrompt.is_active == True)
    )
    prompts = result.scalars().all()
    if not prompts:
        raise HTTPException(400, "활성 프롬프트가 없습니다. 먼저 프롬프트를 추가하세요.")

    # 엔진 인스턴스 생성
    engines = _get_enabled_engines()
    if not engines:
        raise HTTPException(500, "활성화된 AI 엔진이 없습니다. API 키를 확인하세요.")

    scan_results: list[dict] = []
    brand_name_lower = brand.name.lower()
    competitor_names = [c.lower() for c in (brand.competitors or [])]

    for prompt_obj in prompts:
        for engine in engines:
            try:
                resp = await engine.query(prompt_obj.prompt_text)
                resp_lower = resp.response_text.lower()

                # 브랜드 언급 여부
                mentioned = brand_name_lower in resp_lower
                mention_count = resp_lower.count(brand_name_lower)

                # 간단한 감성 분석 (키워드 기반)
                sentiment = _estimate_sentiment(resp.response_text, brand.name)

                sr = ScanResult(
                    scan_prompt_id=prompt_obj.id,
                    engine=engine.name,
                    response_text=resp.response_text,
                    brand_mentioned=mentioned,
                    mention_count=mention_count,
                    sentiment_score=sentiment,
                    citation_urls=resp.citations,
                    scanned_at=datetime.utcnow(),
                )
                session.add(sr)
                scan_results.append({
                    "prompt": prompt_obj.prompt_text,
                    "engine": engine.name,
                    "mentioned": mentioned,
                    "sentiment": sentiment,
                })
            except Exception as e:
                logger.error("스캔 실패 [%s / %s]: %s", engine.name, prompt_obj.prompt_text[:30], e)
                scan_results.append({
                    "prompt": prompt_obj.prompt_text,
                    "engine": engine.name,
                    "error": str(e),
                })

    # Visibility 점수 계산
    await _calculate_visibility(session, brand, competitor_names)
    await session.commit()

    return {
        "brand_id": brand_id,
        "total_queries": len(scan_results),
        "results": scan_results,
    }


def _get_enabled_engines():
    """활성화된 엔진 인스턴스 리스트 반환."""
    engines = []
    if AI_ENGINES.get("chatgpt", {}).get("enabled"):
        from app.engines.openai_engine import OpenAIEngine
        engines.append(OpenAIEngine())
    if AI_ENGINES.get("gemini", {}).get("enabled"):
        from app.engines.gemini_engine import GeminiEngine
        engines.append(GeminiEngine())
    if AI_ENGINES.get("perplexity", {}).get("enabled"):
        from app.engines.perplexity_engine import PerplexityEngine
        engines.append(PerplexityEngine())
    if AI_ENGINES.get("claude", {}).get("enabled"):
        from app.engines.claude_engine import ClaudeEngine
        engines.append(ClaudeEngine())
    return engines


def _estimate_sentiment(text: str, brand_name: str) -> float:
    """간단한 키워드 기반 감성 점수 (-1.0 ~ +1.0)."""
    positive_words = ["추천", "좋은", "인기", "우수", "만족", "최고", "훌륭", "뛰어난", "사랑", "강력"]
    negative_words = ["단점", "아쉬운", "별로", "비싼", "불만", "최악", "부족", "문제", "위험", "주의"]

    pos = sum(1 for w in positive_words if w in text)
    neg = sum(1 for w in negative_words if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 2)


async def _calculate_visibility(
    session: AsyncSession, brand: Brand, competitor_names: list[str]
):
    """스캔 결과를 집계하여 VisibilityScore 레코드 생성."""
    today = date.today()
    result = await session.execute(
        select(ScanResult)
        .join(ScanPrompt)
        .where(
            ScanPrompt.brand_id == brand.id,
            func.date(ScanResult.scanned_at) == today,
        )
    )
    all_results = result.scalars().all()

    # 엔진별 집계
    engine_groups: dict[str, list[ScanResult]] = {}
    for sr in all_results:
        engine_groups.setdefault(sr.engine, []).append(sr)

    for engine_name, results_list in engine_groups.items():
        total = len(results_list)
        mentioned = sum(1 for r in results_list if r.brand_mentioned)
        avg_sent = sum(r.sentiment_score or 0 for r in results_list) / total if total else 0
        visibility = (mentioned / total * 100) if total else 0

        # SoV: 자사 언급 수 / (자사 + 경쟁사 언급 수)
        competitor_mentions = 0
        for r in results_list:
            resp_lower = r.response_text.lower()
            for comp in competitor_names:
                if comp in resp_lower:
                    competitor_mentions += 1

        total_mentions = mentioned + competitor_mentions
        sov = (mentioned / total_mentions * 100) if total_mentions > 0 else 0

        vs = VisibilityScore(
            brand_id=brand.id,
            engine=engine_name,
            score=round(visibility, 1),
            share_of_voice=round(sov, 1),
            avg_sentiment=round(avg_sent, 2),
            total_prompts=total,
            mentioned_prompts=mentioned,
            period_start=today,
            period_end=today,
            calculated_at=datetime.utcnow(),
        )
        session.add(vs)


# ---------------------------------------------------------------------------
# Score history & latest
# ---------------------------------------------------------------------------

@router.get("/api/scores/{brand_id}")
async def get_score_history(brand_id: int, session: AsyncSession = Depends(get_session)):
    """점수 히스토리 (엔진별)."""
    result = await session.execute(
        select(VisibilityScore)
        .where(VisibilityScore.brand_id == brand_id)
        .order_by(VisibilityScore.period_start.asc())
    )
    scores = result.scalars().all()

    history: dict[str, list[dict]] = {}
    for s in scores:
        entry = {
            "date": s.period_start.isoformat() if s.period_start else None,
            "score": s.score,
            "sov": s.share_of_voice,
            "sentiment": s.avg_sentiment,
            "total": s.total_prompts,
            "mentioned": s.mentioned_prompts,
        }
        history.setdefault(s.engine, []).append(entry)

    return {"brand_id": brand_id, "history": history}


@router.get("/api/scores/{brand_id}/latest")
async def get_latest_scores(brand_id: int, session: AsyncSession = Depends(get_session)):
    """최신 점수 요약."""
    result = await session.execute(
        select(VisibilityScore)
        .where(VisibilityScore.brand_id == brand_id)
        .order_by(VisibilityScore.calculated_at.desc())
    )
    all_scores = result.scalars().all()

    latest: dict[str, dict] = {}
    for s in all_scores:
        if s.engine not in latest:
            latest[s.engine] = {
                "engine": s.engine,
                "score": s.score,
                "share_of_voice": s.share_of_voice,
                "avg_sentiment": s.avg_sentiment,
                "total_prompts": s.total_prompts,
                "mentioned_prompts": s.mentioned_prompts,
                "date": s.period_start.isoformat() if s.period_start else None,
            }

    # 전체 평균
    if latest:
        avg_score = round(sum(v["score"] for v in latest.values()) / len(latest), 1)
        avg_sov = round(sum(v["share_of_voice"] for v in latest.values()) / len(latest), 1)
        avg_sent = round(sum(v["avg_sentiment"] for v in latest.values()) / len(latest), 2)
        total_prompts = sum(v["total_prompts"] for v in latest.values())
    else:
        avg_score = avg_sov = avg_sent = 0
        total_prompts = 0

    return {
        "brand_id": brand_id,
        "summary": {
            "avg_visibility": avg_score,
            "avg_sov": avg_sov,
            "avg_sentiment": avg_sent,
            "total_prompts_tracked": total_prompts,
        },
        "engines": latest,
    }


# ---------------------------------------------------------------------------
# Scan results detail
# ---------------------------------------------------------------------------

@router.get("/api/results/{brand_id}")
async def get_scan_results(brand_id: int, limit: int = 50, session: AsyncSession = Depends(get_session)):
    """최근 스캔 결과 상세."""
    result = await session.execute(
        select(ScanResult)
        .join(ScanPrompt)
        .where(ScanPrompt.brand_id == brand_id)
        .order_by(ScanResult.scanned_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()

    # prompt_text 조회를 위해 prompt ID 수집
    prompt_ids = {r.scan_prompt_id for r in rows}
    prompt_result = await session.execute(
        select(ScanPrompt).where(ScanPrompt.id.in_(prompt_ids))
    )
    prompt_map = {p.id: p.prompt_text for p in prompt_result.scalars().all()}

    return [
        {
            "id": r.id,
            "prompt_text": prompt_map.get(r.scan_prompt_id, ""),
            "engine": r.engine,
            "brand_mentioned": r.brand_mentioned,
            "mention_count": r.mention_count,
            "sentiment_score": r.sentiment_score,
            "response_text": r.response_text[:500],
            "citation_urls": r.citation_urls or [],
            "scanned_at": r.scanned_at.isoformat() if r.scanned_at else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Competitor comparison
# ---------------------------------------------------------------------------

@router.get("/api/competitors/{brand_id}")
async def get_competitor_data(brand_id: int, session: AsyncSession = Depends(get_session)):
    """경쟁사 비교 데이터."""
    brand = await session.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다.")

    competitors = brand.competitors or []

    # 최근 스캔 결과에서 경쟁사 언급 횟수 집계
    result = await session.execute(
        select(ScanResult)
        .join(ScanPrompt)
        .where(ScanPrompt.brand_id == brand_id)
        .order_by(ScanResult.scanned_at.desc())
        .limit(200)
    )
    rows = result.scalars().all()

    mention_counts: dict[str, int] = {brand.name: 0}
    for c in competitors:
        mention_counts[c] = 0

    for r in rows:
        resp_lower = r.response_text.lower()
        if brand.name.lower() in resp_lower:
            mention_counts[brand.name] += 1
        for c in competitors:
            if c.lower() in resp_lower:
                mention_counts[c] += 1

    total_mentions = sum(mention_counts.values())
    shares = {}
    for name, count in mention_counts.items():
        shares[name] = round((count / total_mentions * 100), 1) if total_mentions > 0 else 0

    return {
        "brand_id": brand_id,
        "brand_name": brand.name,
        "competitors": competitors,
        "mention_counts": mention_counts,
        "share_of_voice": shares,
        "total_results_analyzed": len(rows),
    }


# ---------------------------------------------------------------------------
# GEO Optimization Recommendations
# ---------------------------------------------------------------------------

@router.get("/api/recommendations/{brand_id}")
async def get_recommendations(brand_id: int, session: AsyncSession = Depends(get_session)):
    """GEO 최적화 추천 액션 아이템 생성."""
    from app.analysis.optimizer import GEOOptimizer

    brand = await session.get(Brand, brand_id)
    if not brand:
        raise HTTPException(404, "브랜드를 찾을 수 없습니다.")

    # 최신 점수 조회
    score_result = await session.execute(
        select(VisibilityScore)
        .where(VisibilityScore.brand_id == brand_id)
        .order_by(VisibilityScore.calculated_at.desc())
    )
    all_scores = score_result.scalars().all()

    latest: dict[str, Any] = {}
    for s in all_scores:
        if s.engine not in latest:
            latest[s.engine] = s

    if latest:
        avg_visibility = sum(s.score for s in latest.values()) / len(latest)
        avg_sov = sum(s.share_of_voice for s in latest.values()) / len(latest)
        avg_sentiment = sum(s.avg_sentiment for s in latest.values()) / len(latest)
    else:
        avg_visibility = avg_sov = avg_sentiment = 0

    # 스캔 결과 조회
    scan_result = await session.execute(
        select(ScanResult)
        .join(ScanPrompt)
        .where(ScanPrompt.brand_id == brand_id)
        .order_by(ScanResult.scanned_at.desc())
        .limit(100)
    )
    scan_rows = scan_result.scalars().all()

    prompt_ids = {r.scan_prompt_id for r in scan_rows}
    if prompt_ids:
        prompt_result = await session.execute(
            select(ScanPrompt).where(ScanPrompt.id.in_(prompt_ids))
        )
        prompt_map = {p.id: p.prompt_text for p in prompt_result.scalars().all()}
    else:
        prompt_map = {}

    scan_results_list = [
        {
            "engine": r.engine,
            "brand_mentioned": r.brand_mentioned,
            "prompt_text": prompt_map.get(r.scan_prompt_id, ""),
            "sentiment_score": r.sentiment_score,
        }
        for r in scan_rows
    ]

    optimizer = GEOOptimizer()
    recommendations = optimizer.generate_recommendations(
        brand_name=brand.name,
        visibility_score=avg_visibility,
        sov_score=avg_sov,
        sentiment_score=avg_sentiment,
        scan_results=scan_results_list,
        competitors=brand.competitors or [],
    )

    return {
        "brand_id": brand_id,
        "brand_name": brand.name,
        "scores": {
            "visibility": round(avg_visibility, 1),
            "sov": round(avg_sov, 1),
            "sentiment": round(avg_sentiment, 2),
        },
        "recommendations": recommendations,
        "total_count": len(recommendations),
    }


# ---------------------------------------------------------------------------
# HTML Report
# ---------------------------------------------------------------------------

@router.get("/report/{brand_id}", response_class=HTMLResponse)
async def get_html_report(brand_id: int, session: AsyncSession = Depends(get_session)):
    """MindScope GEO HTML 리포트 생성."""
    from app.report import ReportGenerator

    generator = ReportGenerator()
    html = await generator.generate_html_report(brand_id, session)
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Scheduler endpoints
# ---------------------------------------------------------------------------

@router.get("/api/schedule")
async def get_schedule_status():
    """현재 스케줄러 상태 조회."""
    from app.scheduler import get_schedule_status
    return get_schedule_status()


@router.post("/api/schedule/run-now")
async def run_schedule_now():
    """즉시 전체 스캔 실행 (백그라운드)."""
    from app.scheduler import daily_scan_job

    asyncio.create_task(daily_scan_job())
    return {"message": "전체 스캔이 백그라운드에서 시작되었습니다.", "status": "started"}


# ---------------------------------------------------------------------------
# Plan management
# ---------------------------------------------------------------------------

class PlanUpgradeRequest(BaseModel):
    plan: str


@router.get("/api/plans")
async def list_plans():
    """전체 요금제 목록 반환."""
    from app.plans import PLANS, format_price

    result = []
    for key, plan in PLANS.items():
        result.append({
            "id": key,
            "name": plan["name"],
            "price_monthly": plan["price_monthly"],
            "price_display": format_price(key),
            "max_engines": plan["max_engines"],
            "max_prompts": plan["max_prompts"],
            "max_brands": plan["max_brands"],
            "max_competitors": plan["max_competitors"],
            "daily_scan_limit": plan["daily_scan_limit"],
            "auto_scan": plan["auto_scan"],
            "report": plan["report"],
            "features": plan["features"],
        })
    return {"plans": result}


@router.post("/api/plans/upgrade")
async def upgrade_plan(
    body: PlanUpgradeRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_current_user),
):
    """사용자 플랜 변경 (JWT 필수)."""
    from app.plans import PLANS

    target_plan = body.plan.lower()
    if target_plan not in PLANS:
        raise HTTPException(400, f"유효하지 않은 플랜입니다: {body.plan}")

    if target_plan == "enterprise":
        raise HTTPException(
            400,
            "Enterprise 플랜은 별도 문의가 필요합니다. "
            "hesed@hesedhouse.net으로 연락해 주세요.",
        )

    old_plan = current_user.plan or "free"
    current_user.plan = target_plan
    await session.commit()

    return {
        "message": f"플랜이 {old_plan} -> {target_plan}으로 변경되었습니다.",
        "user_id": current_user.id,
        "old_plan": old_plan,
        "new_plan": target_plan,
    }


@router.get("/api/plans/my")
async def my_plan(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_current_user),
):
    """현재 사용자의 플랜 + 사용량 반환."""
    from app.plans import get_plan, format_price

    user_plan_name = current_user.plan or "free"
    plan = get_plan(user_plan_name)

    # 사용량 집계: 브랜드 수
    brand_count_result = await session.execute(
        select(func.count(Brand.id))
        .join(Client)
        .where(Client.user_id == current_user.id)
    )
    brand_count = brand_count_result.scalar() or 0

    # 사용량 집계: 활성 프롬프트 수 (전체 브랜드 합산)
    prompt_count_result = await session.execute(
        select(func.count(ScanPrompt.id))
        .join(Brand)
        .join(Client)
        .where(Client.user_id == current_user.id, ScanPrompt.is_active == True)
    )
    prompt_count = prompt_count_result.scalar() or 0

    # 오늘 스캔 횟수
    today = date.today()
    scan_count_result = await session.execute(
        select(func.count(ScanResult.id))
        .join(ScanPrompt)
        .join(Brand)
        .join(Client)
        .where(
            Client.user_id == current_user.id,
            func.date(ScanResult.scanned_at) == today,
        )
    )
    today_scan_count = scan_count_result.scalar() or 0

    return {
        "plan": user_plan_name,
        "plan_name": plan["name"],
        "price_display": format_price(user_plan_name),
        "limits": {
            "max_engines": plan["max_engines"],
            "max_prompts": plan["max_prompts"],
            "max_brands": plan["max_brands"],
            "max_competitors": plan["max_competitors"],
            "daily_scan_limit": plan["daily_scan_limit"],
            "auto_scan": plan["auto_scan"],
            "report": plan["report"],
        },
        "usage": {
            "brands": brand_count,
            "prompts": prompt_count,
            "today_scans": today_scan_count,
        },
    }


# ---------------------------------------------------------------------------
# Free Diagnosis (no auth required)
# ---------------------------------------------------------------------------

class DiagnoseRequestBody(BaseModel):
    email: str
    name: str
    company_name: str = ""
    phone: str
    brand_name: str
    keywords: list[str]
    competitors: list[str] = []


class DiagnoseVerifyBody(BaseModel):
    request_id: int
    code: str


@router.post("/api/diagnose/request")
async def request_diagnosis(
    body: DiagnoseRequestBody,
    session: AsyncSession = Depends(get_session),
):
    """무료 진단 요청 — 이메일 인증코드 발송 (실제 발송은 미구현, debug_code로 반환)."""
    import re
    import random
    from datetime import timedelta

    brand = body.brand_name.strip()
    if not brand:
        raise HTTPException(400, "브랜드명을 입력해주세요.")
    if not body.keywords:
        raise HTTPException(400, "키워드를 1개 이상 입력해주세요.")

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "올바른 이메일을 입력해주세요.")

    if not body.name or not body.name.strip():
        raise HTTPException(400, "담당자명을 입력해주세요.")

    if not body.phone or not body.phone.strip():
        raise HTTPException(400, "휴대폰번호를 입력해주세요.")

    phone_digits = re.sub(r"\D", "", body.phone)
    if not (10 <= len(phone_digits) <= 11):
        raise HTTPException(400, "올바른 휴대폰번호를 입력해주세요. (10~11자리)")

    if not body.company_name or not body.company_name.strip():
        raise HTTPException(400, "회사명을 입력해주세요.")

    # 같은 이메일로 24시간 내 인증 완료된 요청이 있는지 확인
    cutoff = datetime.utcnow() - timedelta(hours=24)
    existing = await session.execute(
        select(DiagnosisRequest).where(
            DiagnosisRequest.email == email,
            DiagnosisRequest.created_at >= cutoff,
            DiagnosisRequest.is_verified == True,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(429, "이미 진단을 요청하셨습니다. 24시간 후 다시 시도해주세요.")

    # 6자리 인증코드 생성
    code = f"{random.randint(0, 999999):06d}"

    diag_req = DiagnosisRequest(
        email=email,
        name=body.name.strip(),
        company_name=body.company_name.strip(),
        phone=phone_digits,
        brand_name=brand,
        keywords=body.keywords,
        competitors=body.competitors,
        verification_code=code,
        is_verified=False,
    )
    session.add(diag_req)
    await session.commit()
    await session.refresh(diag_req)

    # TODO: 실제 이메일 발송 (SendGrid 등) — 현재는 debug_code로 반환
    logger.info("진단 인증코드 발송 (debug): email=%s, code=%s", email, code)

    return {
        "request_id": diag_req.id,
        "message": "인증코드를 이메일로 발송했습니다.",
        "debug_code": code,  # 테스트용 — 프로덕션에서 제거
    }


@router.post("/api/diagnose/verify")
async def verify_and_run_diagnosis(
    body: DiagnoseVerifyBody,
    session: AsyncSession = Depends(get_session),
):
    """인증코드 확인 후 AI 진단 실행."""
    diag_req = await session.get(DiagnosisRequest, body.request_id)
    if not diag_req:
        raise HTTPException(404, "진단 요청을 찾을 수 없습니다.")

    if diag_req.is_verified and diag_req.result_data:
        return diag_req.result_data

    if diag_req.verification_code != body.code.strip():
        raise HTTPException(400, "인증코드가 올바르지 않습니다. 다시 확인해주세요.")

    diag_req.is_verified = True

    # --- 기존 진단 로직 실행 ---
    brand = diag_req.brand_name
    keywords = diag_req.keywords or []
    competitors = diag_req.competitors or []

    # 프롬프트 자동 생성
    prompts: list[str] = []
    for kw in keywords[:5]:
        prompts.append(f"{kw} 추천해줘")
        prompts.append(f"좋은 {kw} 브랜드")
        prompts.append(f"{kw} 순위")

    engines = _get_enabled_engines()
    if not engines:
        raise HTTPException(
            503,
            "현재 AI 엔진이 비활성 상태입니다. 잠시 후 다시 시도해주세요.",
        )

    brand_lower = brand.lower()

    raw_results: list[dict] = []
    engine_mention_counts: dict[str, dict] = {}
    all_mention_counts: dict[str, int] = {brand: 0}
    for comp in competitors:
        if comp.strip():
            all_mention_counts[comp.strip()] = 0

    sentiment_scores: list[float] = []

    for prompt_text in prompts:
        for engine in engines:
            try:
                resp = await engine.query(prompt_text)
                resp_lower = resp.response_text.lower()

                mentioned = brand_lower in resp_lower

                if engine.name not in engine_mention_counts:
                    engine_mention_counts[engine.name] = {"total": 0, "mentioned": 0}
                engine_mention_counts[engine.name]["total"] += 1
                if mentioned:
                    engine_mention_counts[engine.name]["mentioned"] += 1
                    all_mention_counts[brand] = all_mention_counts.get(brand, 0) + 1

                for comp in competitors:
                    comp_clean = comp.strip()
                    if comp_clean and comp_clean.lower() in resp_lower:
                        all_mention_counts[comp_clean] = all_mention_counts.get(comp_clean, 0) + 1

                sent = _estimate_sentiment(resp.response_text, brand)
                sentiment_scores.append(sent)

                raw_results.append({
                    "prompt": prompt_text,
                    "engine": engine.name,
                    "mentioned": mentioned,
                })
            except Exception as e:
                logger.warning("진단 스캔 실패 [%s / %s]: %s", engine.name, prompt_text[:30], e)
                raw_results.append({
                    "prompt": prompt_text,
                    "engine": engine.name,
                    "mentioned": False,
                    "error": str(e),
                })

    total_queries = sum(ec["total"] for ec in engine_mention_counts.values())
    total_mentioned = sum(ec["mentioned"] for ec in engine_mention_counts.values())
    visibility = (total_mentioned / total_queries * 100) if total_queries > 0 else 0

    total_all_mentions = sum(all_mention_counts.values())
    sov_score = (all_mention_counts.get(brand, 0) / total_all_mentions * 100) if total_all_mentions > 0 else 0
    sov_dict: dict[str, int] = {}
    for name, cnt in all_mention_counts.items():
        sov_dict[name] = round((cnt / total_all_mentions * 100), 1) if total_all_mentions > 0 else 0

    avg_sentiment = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0

    overall = round(visibility * 0.5 + sov_score * 0.3 + (avg_sentiment + 1) * 50 * 0.2, 1)
    overall = max(0, min(100, overall))

    engine_scores: dict[str, int] = {}
    for eng_name, counts in engine_mention_counts.items():
        eng_score = round(counts["mentioned"] / counts["total"] * 100) if counts["total"] > 0 else 0
        engine_scores[eng_name] = eng_score

    if overall <= 20:
        grade = "매우 낮음"
    elif overall <= 40:
        grade = "낮음"
    elif overall <= 60:
        grade = "보통"
    elif overall <= 80:
        grade = "높음"
    else:
        grade = "매우 높음"

    projected = round(min(overall * 2.5, 85))
    if overall == 0:
        projected = 35

    improvements = _generate_diagnosis_improvements(overall, sov_score, avg_sentiment, visibility)

    result = {
        "brand_name": brand,
        "overall_score": round(overall),
        "grade": grade,
        "engine_scores": engine_scores,
        "sov": sov_dict,
        "sentiment": round(avg_sentiment, 2),
        "projected_score": projected,
        "improvements": improvements,
        "raw_results": raw_results,
    }

    diag_req.result_data = result
    await session.commit()

    return result


def _generate_diagnosis_improvements(
    overall: float, sov: float, sentiment: float, visibility: float,
) -> list[dict]:
    """진단 결과에 따른 개선 포인트 3개 자동 생성."""
    pool: list[dict] = []

    if visibility < 40:
        pool.append({
            "icon": "\U0001f527",
            "title": "FAQ 구조 최적화",
            "desc": "AI가 인용하기 쉬운 FAQ 형태로 웹사이트 콘텐츠를 재구성하세요. 질문-답변 구조는 AI 답변 인용률을 크게 높입니다.",
        })
        pool.append({
            "icon": "\U0001f4dd",
            "title": "구조화 데이터(Schema) 적용",
            "desc": "제품, 리뷰, FAQ 등의 Schema Markup을 웹사이트에 추가하면 AI가 브랜드 정보를 정확하게 이해하고 인용합니다.",
        })

    if sov < 30:
        pool.append({
            "icon": "\u2694\ufe0f",
            "title": "경쟁사 대비 콘텐츠 갭 해소",
            "desc": "경쟁사가 AI에서 더 많이 언급되고 있습니다. 차별화된 브랜드 스토리와 전문 콘텐츠를 강화하세요.",
        })

    pool.append({
        "icon": "\U0001f916",
        "title": "AI 크롤러 접근성 개선",
        "desc": "AI 엔진이 웹사이트 콘텐츠를 크롤링할 수 있도록 robots.txt, sitemap을 최적화하고 JavaScript 렌더링 의존도를 줄이세요.",
    })
    pool.append({
        "icon": "\u2b50",
        "title": "브랜드 권위 시그널 강화",
        "desc": "업계 전문 매체 기고, 리뷰 사이트 평점, 위키 등록 등 제3자 권위 시그널을 확보하면 AI의 브랜드 추천 확률이 높아집니다.",
    })

    if sentiment < 0.3:
        pool.append({
            "icon": "\U0001f4ac",
            "title": "긍정 리뷰/콘텐츠 확대",
            "desc": "AI는 긍정적 맥락에서 언급된 브랜드를 더 자주 추천합니다. 고객 후기, 수상 이력, 전문가 추천 콘텐츠를 늘리세요.",
        })

    pool.append({
        "icon": "\U0001f4ca",
        "title": "통계/데이터 기반 콘텐츠 제작",
        "desc": "AI는 구체적 수치와 통계가 포함된 콘텐츠를 인용하는 경향이 있습니다. 자체 리서치, 설문 결과 등을 공개하세요.",
    })

    # Return top 3, avoiding duplicates
    seen: set[str] = set()
    result: list[dict] = []
    for item in pool:
        if item["title"] not in seen and len(result) < 3:
            seen.add(item["title"])
            result.append(item)
    return result
