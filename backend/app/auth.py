"""Cookie-backed login dependencies."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import get_session
from .models.user import User
from .models.user_session import UserSession
from .utils import hash_key

SESSION_COOKIE_NAME = "miao_session"
SESSION_TTL_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


async def create_user_session(user: User, session: AsyncSession) -> str:
    token = generate_session_token()
    user_session = UserSession(
        user_id=user.id,
        token_hash=hash_key(token),
        expires_at=_now() + timedelta(days=SESSION_TTL_DAYS),
    )
    session.add(user_session)
    await session.commit()
    return token


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    result = await session.execute(
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(
            UserSession.token_hash == hash_key(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > _now(),
        )
    )
    user_session = result.scalar_one_or_none()
    if not user_session or not user_session.user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user_session.user


async def revoke_current_session(
    request: Request, session: AsyncSession = Depends(get_session)
) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return
    result = await session.execute(
        select(UserSession).where(
            UserSession.token_hash == hash_key(token),
            UserSession.revoked_at.is_(None),
        )
    )
    user_session = result.scalar_one_or_none()
    if user_session:
        user_session.revoked_at = _now()
        await session.commit()
