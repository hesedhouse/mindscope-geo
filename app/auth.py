"""MindScope GEO -- JWT 인증 유틸리티."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import JWT_ALGORITHM, JWT_EXPIRE_HOURS, JWT_SECRET
from app.db.database import get_session
from app.db.models import User

# ---------------------------------------------------------------------------
# Password hashing (bcrypt 직접 사용)
# ---------------------------------------------------------------------------
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """비밀번호를 bcrypt로 해싱."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """평문과 해시된 비밀번호를 비교."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# ---------------------------------------------------------------------------
# JWT token
# ---------------------------------------------------------------------------
def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """JWT 액세스 토큰 생성 (HS256, 기본 24시간 만료)."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=JWT_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ---------------------------------------------------------------------------
# Current user dependency
# ---------------------------------------------------------------------------
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    session: AsyncSession = Depends(get_session),
) -> Optional[User]:
    """JWT에서 user_id를 추출하고 DB에서 User 조회.

    토큰이 없으면 None을 반환 (비인증 접근 허용).
    토큰이 있지만 유효하지 않으면 401 에러.
    """
    if credentials is None:
        return None

    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: int | None = payload.get("user_id")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="유효하지 않은 토큰입니다.",
            )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="토큰이 만료되었거나 유효하지 않습니다.",
        )

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="사용자를 찾을 수 없습니다.",
        )
    return user


async def require_current_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """인증 필수 엔드포인트용 의존성."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    return user
