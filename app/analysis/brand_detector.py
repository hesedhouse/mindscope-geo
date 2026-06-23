import re
from dataclasses import dataclass, field


@dataclass
class BrandAnalysis:
    brand_mentioned: bool = False
    mention_count: int = 0
    competitor_mentions: dict[str, int] = field(default_factory=dict)
    mention_positions: list[int] = field(default_factory=list)


class BrandDetector:
    @staticmethod
    def _count_mentions(text: str, name: str) -> tuple[int, list[int]]:
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        matches = list(pattern.finditer(text))
        return len(matches), [m.start() for m in matches]

    def detect(
        self,
        response_text: str,
        brand_name: str,
        competitors: list[str] | None = None,
    ) -> BrandAnalysis:
        competitors = competitors or []
        count, positions = self._count_mentions(response_text, brand_name)

        competitor_mentions: dict[str, int] = {}
        for comp in competitors:
            comp_count, _ = self._count_mentions(response_text, comp)
            if comp_count > 0:
                competitor_mentions[comp] = comp_count

        return BrandAnalysis(
            brand_mentioned=count > 0,
            mention_count=count,
            competitor_mentions=competitor_mentions,
            mention_positions=positions,
        )
