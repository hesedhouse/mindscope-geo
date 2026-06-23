"""MindScope GEO -- 자동 스캔 스케줄러."""

from __future__ import annotations

import logging
from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import async_session
from app.db.models import Brand, ScanPrompt, ScanResult, VisibilityScore

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# 마지막 스케줄 실행 상태를 메모리에 보관
_last_run: dict = {
    "status": "대기",
    "started_at": None,
    "finished_at": None,
    "brands_scanned": 0,
    "total_queries": 0,
    "errors": 0,
}


def get_schedule_status() -> dict:
    """현재 스케줄러 상태를 반환."""
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run.isoformat() if next_run else None,
        })

    return {
        "running": scheduler.running,
        "jobs": jobs,
        "last_run": _last_run.copy(),
    }


async def daily_scan_job() -> None:
    """모든 활성 브랜드를 순차 스캔."""
    global _last_run
    logger.info("[스케줄러] 일일 자동 스캔 시작")

    _last_run["status"] = "실행 중"
    _last_run["started_at"] = datetime.utcnow().isoformat()
    _last_run["brands_scanned"] = 0
    _last_run["total_queries"] = 0
    _last_run["errors"] = 0

    try:
        async with async_session() as session:
            # 모든 브랜드 조회
            result = await session.execute(
                select(Brand).options(selectinload(Brand.scan_prompts))
            )
            brands = result.scalars().all()

            if not brands:
                logger.info("[스케줄러] 등록된 브랜드 없음, 스캔 건너뜀")
                _last_run["status"] = "완료 (브랜드 없음)"
                _last_run["finished_at"] = datetime.utcnow().isoformat()
                return

            # 엔진 인스턴스
            engines = _get_enabled_engines()
            if not engines:
                logger.warning("[스케줄러] 활성 AI 엔진 없음, 스캔 건너뜀")
                _last_run["status"] = "완료 (엔진 없음)"
                _last_run["finished_at"] = datetime.utcnow().isoformat()
                return

            for brand in brands:
                active_prompts = [p for p in brand.scan_prompts if p.is_active]
                if not active_prompts:
                    continue

                brand_name_lower = brand.name.lower()
                competitor_names = [c.lower() for c in (brand.competitors or [])]

                for prompt_obj in active_prompts:
                    for engine in engines:
                        try:
                            resp = await engine.query(prompt_obj.prompt_text)
                            resp_lower = resp.response_text.lower()

                            mentioned = brand_name_lower in resp_lower
                            mention_count = resp_lower.count(brand_name_lower)
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
                            _last_run["total_queries"] += 1

                        except Exception as e:
                            logger.error(
                                "[스케줄러] 스캔 실패 [%s / %s]: %s",
                                engine.name, prompt_obj.prompt_text[:30], e,
                            )
                            _last_run["errors"] += 1

                # Visibility 점수 계산
                await _calculate_visibility(session, brand, competitor_names)
                _last_run["brands_scanned"] += 1

            await session.commit()

    except Exception as e:
        logger.error("[스케줄러] 일일 스캔 중 오류: %s", e)
        _last_run["status"] = f"오류: {str(e)}"
        _last_run["finished_at"] = datetime.utcnow().isoformat()
        return

    _last_run["status"] = "완료"
    _last_run["finished_at"] = datetime.utcnow().isoformat()
    logger.info(
        "[스케줄러] 일일 스캔 완료: %d 브랜드, %d 쿼리, %d 오류",
        _last_run["brands_scanned"],
        _last_run["total_queries"],
        _last_run["errors"],
    )


def start_scheduler() -> None:
    """스케줄러 시작 -- 매일 오전 9시(KST) 자동 스캔."""
    scheduler.add_job(
        daily_scan_job,
        "cron",
        hour=9,
        minute=0,
        id="daily_scan",
        name="일일 자동 스캔",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[스케줄러] 시작됨 — 매일 09:00 자동 스캔 예약")


def shutdown_scheduler() -> None:
    """스케줄러 종료."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[스케줄러] 종료됨")


# ---------------------------------------------------------------------------
# Internal helpers (routes.py와 동일 로직 재사용)
# ---------------------------------------------------------------------------

def _get_enabled_engines():
    """활성화된 엔진 인스턴스 리스트 반환."""
    from app.config import AI_ENGINES

    engines = []
    if AI_ENGINES.get("chatgpt", {}).get("enabled"):
        from app.engines.openai_engine import OpenAIEngine
        engines.append(OpenAIEngine())
    if AI_ENGINES.get("gemini", {}).get("enabled"):
        from app.engines.gemini_engine import GeminiEngine
        engines.append(GeminiEngine())
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
    session, brand: Brand, competitor_names: list[str]
) -> None:
    """스캔 결과를 집계하여 VisibilityScore 레코드 생성."""
    from sqlalchemy import func

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

    engine_groups: dict[str, list[ScanResult]] = {}
    for sr in all_results:
        engine_groups.setdefault(sr.engine, []).append(sr)

    for engine_name, results_list in engine_groups.items():
        total = len(results_list)
        mentioned = sum(1 for r in results_list if r.brand_mentioned)
        avg_sent = sum(r.sentiment_score or 0 for r in results_list) / total if total else 0
        visibility = (mentioned / total * 100) if total else 0

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
