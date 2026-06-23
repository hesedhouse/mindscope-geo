"""MindScope GEO — FastAPI 앱 엔트리포인트."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.api.routes import router
from app.api.auth_routes import auth_router
from app.db.database import init_db
from app.scheduler import start_scheduler, shutdown_scheduler

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작 시 DB 초기화 + 스케줄러 시작."""
    await init_db()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="MindScope GEO",
    description="AI 검색 엔진 브랜드 가시성 모니터링 대시보드",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static & Templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.state.templates = templates

# Routers
app.include_router(auth_router)
app.include_router(router)


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """로그인/회원가입 페이지."""
    return templates.TemplateResponse(request, "login.html")


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    """요금제 페이지."""
    return templates.TemplateResponse(request, "pricing.html")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    """개인정보 처리방침."""
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/diagnose", response_class=HTMLResponse)
async def diagnose_page(request: Request):
    """무료 AI 가시성 진단 페이지."""
    return templates.TemplateResponse(request, "diagnose.html")


if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
