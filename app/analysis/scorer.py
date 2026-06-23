from datetime import date, datetime
from dataclasses import dataclass
from app.db.models import ScanResult, VisibilityScore


@dataclass
class ScoreResult:
    visibility: float
    share_of_voice: float
    avg_sentiment: float
    total_prompts: int
    mentioned_prompts: int


class GEOScorer:
    @staticmethod
    def calculate_visibility(scan_results: list[ScanResult]) -> float:
        if not scan_results:
            return 0.0
        mentioned = sum(1 for r in scan_results if r.brand_mentioned)
        return (mentioned / len(scan_results)) * 100

    @staticmethod
    def calculate_sov(
        scan_results: list[ScanResult],
        brand_mention_total: int,
        competitor_mention_total: int,
    ) -> float:
        total_mentions = brand_mention_total + competitor_mention_total
        if total_mentions == 0:
            return 0.0
        return (brand_mention_total / total_mentions) * 100

    @staticmethod
    def calculate_sentiment(scan_results: list[ScanResult]) -> float:
        scores = [r.sentiment_score for r in scan_results if r.sentiment_score is not None]
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def generate_score(
        self,
        brand_id: int,
        engine: str,
        scan_results: list[ScanResult],
        brand_mention_total: int,
        competitor_mention_total: int,
        period_start: date,
        period_end: date,
    ) -> VisibilityScore:
        mentioned = sum(1 for r in scan_results if r.brand_mentioned)
        return VisibilityScore(
            brand_id=brand_id,
            engine=engine,
            score=self.calculate_visibility(scan_results),
            share_of_voice=self.calculate_sov(
                scan_results, brand_mention_total, competitor_mention_total
            ),
            avg_sentiment=self.calculate_sentiment(scan_results),
            total_prompts=len(scan_results),
            mentioned_prompts=mentioned,
            period_start=period_start,
            period_end=period_end,
            calculated_at=datetime.utcnow(),
        )
