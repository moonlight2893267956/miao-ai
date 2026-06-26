"""Simple database-backed login API."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    clear_session_cookie,
    create_user_session,
    get_current_user,
    revoke_current_session,
    set_session_cookie,
)
from ..db import get_session
from ..models.user import User
from ..schemas.auth import AuthStatus, AuthUser, LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_status(user: User) -> AuthStatus:
    return AuthStatus(
        user=AuthUser(id=user.id, username=user.username, created_at=user.created_at)
    )


@router.post("/login", response_model=AuthStatus)
async def login(
    payload: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AuthStatus:
    result = await session.execute(
        select(User).where(User.username == payload.username, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user or user.password != payload.password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    token = await create_user_session(user, session)
    set_session_cookie(response, token)
    return _auth_status(user)


@router.get("/me", response_model=AuthStatus)
async def me(user: User = Depends(get_current_user)) -> AuthStatus:
    return _auth_status(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    _: None = Depends(revoke_current_session),
) -> None:
    clear_session_cookie(response)
