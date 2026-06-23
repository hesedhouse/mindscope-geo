from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
PERPLEXITY_API_KEY: str = os.getenv("PERPLEXITY_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

JWT_SECRET: str = os.getenv("JWT_SECRET", os.urandom(32).hex())
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = 24

# Social Login
KAKAO_CLIENT_ID: str = os.getenv("KAKAO_CLIENT_ID", "")
KAKAO_CLIENT_SECRET: str = os.getenv("KAKAO_CLIENT_SECRET", "")
NAVER_CLIENT_ID: str = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET: str = os.getenv("NAVER_CLIENT_SECRET", "")

# 서비스 베이스 URL (OAuth callback에 사용)
BASE_URL: str = os.getenv("BASE_URL", "https://geo.mindscopekorea.com")

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{Path(__file__).resolve().parent.parent / 'mindscope_geo.db'}",
)

AI_ENGINES: dict = {
    "chatgpt": {
        "model": "gpt-4o",
        "enabled": bool(OPENAI_API_KEY),
    },
    "gemini": {
        "model": "gemini-2.5-flash",
        "enabled": bool(GEMINI_API_KEY),
    },
    "perplexity": {
        "model": "sonar-pro",
        "enabled": bool(PERPLEXITY_API_KEY),
    },
    "claude": {
        "model": "claude-sonnet-4-20250514",
        "enabled": bool(ANTHROPIC_API_KEY),
    },
}

SYSTEM_PROMPT_KO: str = (
    "당신은 한국 소비자에게 도움을 주는 AI 어시스턴트입니다. "
    "질문에 대해 구체적인 브랜드명과 제품명을 포함하여 추천해주세요."
)
