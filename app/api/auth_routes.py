"""MindScope GEO -- 인증 API 라우터."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    hash_password,
    require_current_user,
    verify_password,
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

    if not user or not verify_password(body.password, user.hashed_password):
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
