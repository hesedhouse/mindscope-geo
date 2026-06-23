import logging
from datetime import date, datetime
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.database import async_session
from app.db.models import Brand, ScanPrompt, ScanResult, VisibilityScore
from app.engines.engine_manager import EngineManager
from app.analysis.brand_detector import BrandDetector
from app.analysis.sentiment import SentimentAnalyzer
from app.analysis.scorer import GEOScorer

logger = logging.getLogger(__name__)


class GEOScanner:
    def __init__(self) -> None:
        self.engine_manager = EngineManager()
        self.brand_detector = BrandDetector()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.scorer = GEOScorer()

    async def scan_brand(self, brand_id: int) -> None:
        async with async_session() as session:
            brand = await session.get(
                Brand, brand_id, options=[selectinload(Brand.scan_prompts)]
            )
            if not brand:
                logger.error("Brand not found: %d", brand_id)
                return

            active_prompts = [p for p in brand.scan_prompts if p.is_active]
            if not active_prompts:
                logger.warning("No active prompts for brand: %s", brand.name)
                return

            logger.info(
                "Scanning brand '%s' with %d prompts across %s",
                brand.name, len(active_prompts), self.engine_manager.active_engines,
            )

            all_results: list[ScanResult] = []
            results_by_engine: dict[str, list[ScanResult]] = defaultdict(list)

            total_brand_mentions = 0
            total_competitor_mentions = 0

            for prompt in active_prompts:
                try:
                    responses = await self.engine_manager.query_all(prompt.prompt_text)
                except Exception:
                    logger.exception("Failed to query engines for prompt %d", prompt.id)
                    continue

                for resp in responses:
                    try:
                        analysis = self.brand_detector.detect(
                            resp.response_text, brand.name, brand.competitors or []
                        )
                        sentiment = await self.sentiment_analyzer.analyze(
                            resp.response_text, brand.name
                        )

                        comp_mention_sum = sum(analysis.competitor_mentions.values())
                        total_brand_mentions += analysis.mention_count
                        total_competitor_mentions += comp_mention_sum

                        result = ScanResult(
                            scan_prompt_id=prompt.id,
                            engine=resp.engine_name,
                            response_text=resp.response_text,
                            brand_mentioned=analysis.brand_mentioned,
                            mention_count=analysis.mention_count,
                            sentiment_score=sentiment,
                            citation_urls=resp.citations,
                            scanned_at=datetime.utcnow(),
                        )
                        session.add(result)
                        all_results.append(result)
                        results_by_engine[resp.engine_name].append(result)
                    except Exception:
                        logger.exception(
                            "Analysis failed for engine=%s prompt=%d",
                            resp.engine_name, prompt.id,
                        )

            today = date.today()
            for engine_name, engine_results in results_by_engine.items():
                engine_brand = sum(r.mention_count for r in engine_results)
                engine_comp = total_competitor_mentions  # simplified approximation
                score = self.scorer.generate_score(
                    brand_id=brand.id,
                    engine=engine_name,
                    scan_results=engine_results,
                    brand_mention_total=engine_brand,
                    competitor_mention_total=engine_comp,
                    period_start=today,
                    period_end=today,
                )
                session.add(score)

            await session.commit()
            logger.info(
                "Scan complete for brand '%s': %d results saved", brand.name, len(all_results)
            )

    async def scan_all(self) -> None:
        async with async_session() as session:
            stmt = select(Brand.id).where(Brand.competitors.isnot(None))
            result = await session.execute(stmt)
            brand_ids = [row[0] for row in result.fetchall()]

        logger.info("Starting full scan for %d brands", len(brand_ids))
        for bid in brand_ids:
            try:
                await self.scan_brand(bid)
            except Exception:
                logger.exception("Scan failed for brand_id=%d", bid)
