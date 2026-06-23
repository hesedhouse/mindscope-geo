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
from app.db.models import Brand, Client, ScanPrompt, ScanResult, User, VisibilityScore
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
