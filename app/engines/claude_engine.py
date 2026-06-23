"""Anthropic Claude AI 엔진."""

import anthropic

from app.config import ANTHROPIC_API_KEY, AI_ENGINES, SYSTEM_PROMPT_KO
from app.engines.base import BaseEngine, EngineResponse


class ClaudeEngine(BaseEngine):
    name = "claude"

    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        self._model = AI_ENGINES["claude"]["model"]

    async def query(self, prompt: str) -> EngineResponse:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=SYSTEM_PROMPT_KO,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""

        return EngineResponse(
            engine_name=self.name,
            response_text=text,
            citations=[],
            raw_data={"model": self._model},
        )
