import logging
from google import genai
from google.genai import types
from app.config import GEMINI_API_KEY, AI_ENGINES, SYSTEM_PROMPT_KO
from app.engines.base import BaseEngine, EngineResponse

logger = logging.getLogger(__name__)


class GeminiEngine(BaseEngine):
    name = "gemini"

    def __init__(self) -> None:
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self._model = AI_ENGINES["gemini"]["model"]

    async def query(self, prompt: str) -> EngineResponse:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT_KO,
                temperature=0.7,
                max_output_tokens=2048,
            ),
        )
        text = response.text or ""

        return EngineResponse(
            engine_name=self.name,
            response_text=text,
            citations=[],
            raw_data={
                "model": self._model,
            },
        )
