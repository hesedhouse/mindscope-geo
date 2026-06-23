from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EngineResponse:
    engine_name: str
    response_text: str
    citations: list[str] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


class BaseEngine(ABC):
    name: str

    @abstractmethod
    async def query(self, prompt: str) -> EngineResponse:
        ...
