import asyncio
import logging
from app.config import AI_ENGINES
from app.engines.base import BaseEngine, EngineResponse
from app.engines.openai_engine import OpenAIEngine
from app.engines.gemini_engine import GeminiEngine

logger = logging.getLogger(__name__)


class EngineManager:
    def __init__(self) -> None:
        self._engines: list[BaseEngine] = []
        self._init_engines()

    def _init_engines(self) -> None:
        engine_map: dict[str, type[BaseEngine]] = {
            "chatgpt": OpenAIEngine,
            "gemini": GeminiEngine,
        }
        for name, cfg in AI_ENGINES.items():
            if cfg["enabled"] and name in engine_map:
                try:
                    self._engines.append(engine_map[name]())
                    logger.info("Engine initialized: %s", name)
                except Exception:
                    logger.exception("Failed to initialize engine: %s", name)

    @property
    def active_engines(self) -> list[str]:
        return [e.name for e in self._engines]

    async def query_all(self, prompt: str) -> list[EngineResponse]:
        async def _safe_query(engine: BaseEngine) -> EngineResponse | None:
            try:
                return await engine.query(prompt)
            except Exception:
                logger.exception("Engine %s failed for prompt: %s", engine.name, prompt[:80])
                return None

        results = await asyncio.gather(*[_safe_query(e) for e in self._engines])
        return [r for r in results if r is not None]
