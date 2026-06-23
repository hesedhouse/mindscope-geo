"""Perplexity AI 엔진 — OpenAI 호환 API (검색 기반 답변 + citations)."""

from openai import AsyncOpenAI

from app.config import PERPLEXITY_API_KEY, AI_ENGINES, SYSTEM_PROMPT_KO
from app.engines.base import BaseEngine, EngineResponse


class PerplexityEngine(BaseEngine):
    name = "perplexity"

    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=PERPLEXITY_API_KEY,
            base_url="https://api.perplexity.ai",
        )
        self._model = AI_ENGINES["perplexity"]["model"]

    async def query(self, prompt: str) -> EngineResponse:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_KO},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2048,
        )
        text = resp.choices[0].message.content or ""

        # Perplexity citations 추출
        citations: list[str] = []
        if hasattr(resp, "citations") and resp.citations:
            citations = resp.citations

        return EngineResponse(
            engine_name=self.name,
            response_text=text,
            citations=citations,
            raw_data={"model": self._model},
        )
