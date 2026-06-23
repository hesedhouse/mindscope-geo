"""MindScope GEO -- 인증 API 라우터."""

from __future__ import annotations

import json
import secrets
from urllib.parse import urlencode, quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    hash_password,
    require_current_user,
    verify_password,
)
from app.config import (
    KAKAO_CLIENT_ID,
    KAKAO_CLIENT_SECRET,
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
    BASE_URL,
)
from app.db.database import get_session
from app.db.models import User

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str
    company_name: str | None = None
    name: str
    phone: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    company_name: str | None
    name: str | None
    phone: str | None
    plan: str

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    token: str
    user: UserResponse


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@auth_router.post("/register", response_model=AuthResponse)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    """회원가입 -- User 생성 + JWT 반환."""
    # 이메일 중복 확인
    result = await session.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 등록된 이메일입니다.",
        )

    if len(body.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="비밀번호는 6자 이상이어야 합니다.",
        )

    # name / phone 필수값 검증
    if not body.name or not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="담당자명을 입력해주세요.",
        )
    if not body.phone or not body.phone.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="휴대폰번호를 입력해주세요.",
        )

    import re
    phone_digits = re.sub(r"\D", "", body.phone)
    if not (10 <= len(phone_digits) <= 11):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="올바른 휴대폰번호를 입력해주세요. (10~11자리)",
        )

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        company_name=body.company_name,
        name=body.name.strip(),
        phone=phone_digits,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token({"user_id": user.id, "email": user.email})

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            company_name=user.company_name,
            name=user.name,
            phone=user.phone,
            plan=user.plan,
        ),
    )


@auth_router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> AuthResponse:
    """로그인 -- JWT 반환."""
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다.",
        )

    token = create_access_token({"user_id": user.id, "email": user.email})

    return AuthResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            company_name=user.company_name,
            name=user.name,
            phone=user.phone,
            plan=user.plan,
        ),
    )


@auth_router.get("/me", response_model=UserResponse)
async def get_me(
    user: User = Depends(require_current_user),
) -> UserResponse:
    """현재 로그인된 사용자 정보 반환."""
    return UserResponse(
        id=user.id,
        email=user.email,
        company_name=user.company_name,
        name=user.name,
        phone=user.phone,
        plan=user.plan,
    )


# ---------------------------------------------------------------------------
# Helper: 소셜 로그인 후 JWT 발급 + 프론트엔드 리다이렉트
# ---------------------------------------------------------------------------

async def _social_login_or_register(
    provider: str,
    provider_id: str,
    email: str | None,
    name: str | None,
    phone: str | None,
    next_url: str | None,
    session: AsyncSession,
) -> RedirectResponse:
    """소셜 로그인 공통: 기존 유저 찾거나 자동 가입 후 JWT 포함 리다이렉트."""
    # 1. provider + provider_id로 기존 유저 검색
    result = await session.execute(
        select(User).where(User.provider == provider, User.provider_id == provider_id)
    )
    user = result.scalar_one_or_none()

    # 2. 없으면 이메일로도 검색 (이메일 로그인 유저가 소셜 연동하는 경우)
    if not user and email:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user and user.provider == "email":
            # 기존 이메일 유저에 소셜 정보 연결
            user.provider = provider
            user.provider_id = provider_id

    # 3. 완전히 새 유저 → 자동 가입
    if not user:
        user = User(
            email=email or f"{provider}_{provider_id}@social.local",
            hashed_password=None,
            provider=provider,
            provider_id=provider_id,
            name=name,
            phone=phone,
        )
        session.add(user)

    await session.commit()
    await session.refresh(user)

    token = create_access_token({"user_id": user.id, "email": user.email})
    user_json = json.dumps(
        {
            "id": user.id,
            "email": user.email,
            "company_name": user.company_name,
            "name": user.name,
            "phone": user.phone,
            "plan": user.plan,
        },
        ensure_ascii=False,
    )

    redirect_path = next_url or "/login"
    separator = "&" if "?" in redirect_path else "?"
    redirect_url = f"{redirect_path}{separator}token={token}&user={quote(user_json)}"
    return RedirectResponse(url=redirect_url, status_code=302)


# ---------------------------------------------------------------------------
# 카카오 OAuth
# ---------------------------------------------------------------------------

@auth_router.get("/kakao")
async def kakao_login(next: str | None = Query(None)):
    """카카오 OAuth 인증 페이지로 리다이렉트."""
    if not KAKAO_CLIENT_ID:
        raise HTTPException(status_code=501, detail="카카오 로그인이 설정되지 않았습니다.")

    callback_url = f"{BASE_URL}/api/auth/kakao/callback"
    state = next or ""
    params = {
        "client_id": KAKAO_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "state": state,
    }
    auth_url = f"https://kauth.kakao.com/oauth/authorize?{urlencode(params)}"
    return RedirectResponse(url=auth_url, status_code=302)


@auth_router.get("/kakao/callback")
async def kakao_callback(
    code: str = Query(...),
    state: str = Query(""),
    session: AsyncSession = Depends(get_session),
):
    """카카오 OAuth 콜백 -- 코드 → 토큰 → 사용자 정보 → JWT."""
    callback_url = f"{BASE_URL}/api/auth/kakao/callback"

    # 1. code → access_token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": KAKAO_CLIENT_ID,
                "client_secret": KAKAO_CLIENT_SECRET,
                "redirect_uri": callback_url,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="카카오 토큰 요청 실패")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="카카오 access_token 없음")

    # 2. access_token → 사용자 정보
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if user_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="카카오 사용자 정보 조회 실패")

    kakao_data = user_resp.json()
    kakao_id = str(kakao_data.get("id", ""))
    kakao_account = kakao_data.get("kakao_account", {})
    properties = kakao_data.get("properties", {})
    email = kakao_account.get("email")
    name = properties.get("nickname")

    next_url = state if state else None
    return await _social_login_or_register(
        provider="kakao",
        provider_id=kakao_id,
        email=email,
        name=name,
        phone=None,
        next_url=next_url,
        session=session,
    )


# ---------------------------------------------------------------------------
# 네이버 OAuth
# ---------------------------------------------------------------------------

@auth_router.get("/naver")
async def naver_login(next: str | None = Query(None)):
    """네이버 OAuth 인증 페이지로 리다이렉트."""
    if not NAVER_CLIENT_ID:
        raise HTTPException(status_code=501, detail="네이버 로그인이 설정되지 않았습니다.")

    callback_url = f"{BASE_URL}/api/auth/naver/callback"
    # state에 CSRF 토큰 + next URL 인코딩
    state_data = json.dumps({"csrf": secrets.token_urlsafe(16), "next": next or ""})
    params = {
        "client_id": NAVER_CLIENT_ID,
        "redirect_uri": callback_url,
        "response_type": "code",
        "state": state_data,
    }
    auth_url = f"https://nid.naver.com/oauth2.0/authorize?{urlencode(params)}"
    return RedirectResponse(url=auth_url, status_code=302)


@auth_router.get("/naver/callback")
async def naver_callback(
    code: str = Query(...),
    state: str = Query("{}"),
    session: AsyncSession = Depends(get_session),
):
    """네이버 OAuth 콜백 -- 코드 → 토큰 → 사용자 정보 → JWT."""
    # state 파싱
    try:
        state_data = json.loads(state)
    except (json.JSONDecodeError, TypeError):
        state_data = {}

    next_url = state_data.get("next") or None

    # 1. code → access_token
    async with httpx.AsyncClient() as client:
        token_resp = await client.get(
            "https://nid.naver.com/oauth2.0/token",
            params={
                "grant_type": "authorization_code",
                "client_id": NAVER_CLIENT_ID,
                "client_secret": NAVER_CLIENT_SECRET,
                "code": code,
                "state": state,
            },
        )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="네이버 토큰 요청 실패")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="네이버 access_token 없음")

    # 2. access_token → 사용자 정보
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://openapi.naver.com/v1/nid/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if user_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="네이버 사용자 정보 조회 실패")

    naver_data = user_resp.json().get("response", {})
    naver_id = str(naver_data.get("id", ""))
    email = naver_data.get("email")
    name = naver_data.get("name")
    mobile = naver_data.get("mobile")
    # 네이버 전화번호에서 하이픈 제거
    phone = mobile.replace("-", "") if mobile else None

    return await _social_login_or_register(
        provider="naver",
        provider_id=naver_id,
        email=email,
        name=name,
        phone=phone,
        next_url=next_url,
        session=session,
    )
