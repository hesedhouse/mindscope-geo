import logging
from openai import AsyncOpenAI
from app.config import OPENAI_API_KEY, AI_ENGINES, SYSTEM_PROMPT_KO
from app.engines.base import BaseEngine, EngineResponse

logger = logging.getLogger(__name__)


class OpenAIEngine(BaseEngine):
    name = "chatgpt"

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        self._model = AI_ENGINES["chatgpt"]["model"]

    async def query(self, prompt: str) -> EngineResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_KO},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2048,
        )
        choice = response.choices[0]
        text = choice.message.content or ""

        return EngineResponse(
            engine_name=self.name,
            response_text=text,
            citations=[],
            raw_data={
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                } if response.usage else {},
                "finish_reason": choice.finish_reason,
            },
        )
