import logging
import re
from functools import lru_cache
from openai import AsyncOpenAI
from app.config import OPENAI_API_KEY

logger = logging.getLogger(__name__)

_SENTIMENT_PROMPT = (
    "다음 텍스트에서 '{brand}' 브랜드에 대한 감성을 분석하세요.\n"
    "점수를 -1.0(매우 부정)에서 1.0(매우 긍정) 사이 소수점 둘째 자리까지 숫자 하나만 출력하세요.\n"
    "중립이면 0.0을 출력하세요.\n\n"
    "텍스트:\n{context}"
)


class SentimentAnalyzer:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self._cache: dict[str, float] = {}

    def _extract_context(self, text: str, brand_name: str, window: int = 50) -> str:
        pattern = re.compile(re.escape(brand_name), re.IGNORECASE)
        segments: list[str] = []
        for match in pattern.finditer(text):
            start = max(0, match.start() - window)
            end = min(len(text), match.end() + window)
            segments.append(text[start:end])
        return " ... ".join(segments) if segments else text[:200]

    async def analyze(self, response_text: str, brand_name: str) -> float:
        cache_key = f"{hash(response_text)}:{brand_name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        context = self._extract_context(response_text, brand_name)
        prompt = _SENTIMENT_PROMPT.format(brand=brand_name, context=context)

        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10,
            )
            raw = (response.choices[0].message.content or "0.0").strip()
            score = float(re.search(r"-?\d+\.?\d*", raw).group())  # type: ignore[union-attr]
            score = max(-1.0, min(1.0, score))
        except Exception:
            logger.exception("Sentiment analysis failed for brand: %s", brand_name)
            score = 0.0

        self._cache[cache_key] = score
        return score
