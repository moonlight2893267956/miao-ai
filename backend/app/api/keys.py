"""
API Key 管理。

- 存储：sha256 哈希（不存明文）
- 颁发：secrets 生成强随机 key，前缀 `miao_`
- 展示：明文只在创建时返回一次（`ApiKeyWithSecret`），之后只能查哈希 id / label / 时间
"""
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.api_key import ApiKey
from ..schemas.api_key import ApiKeyCreate, ApiKeyRead, ApiKeyWithSecret
from ..utils import get_agent_or_404, hash_key

router = APIRouter(prefix="/agents/{name}/keys", tags=["api-keys"])


def _generate_key() -> str:
    # 32 字节随机 → ~43 字符 base64
    return f"miao_{secrets.token_urlsafe(32)}"


@router.get("", response_model=list[ApiKeyRead])
async def list_keys(
    name: str, session: AsyncSession = Depends(get_session)
) -> list[ApiKey]:
    agent = await get_agent_or_404(name, session)
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.agent_id == agent.id, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=ApiKeyWithSecret, status_code=status.HTTP_201_CREATED)
async def create_key(
    name: str,
    payload: ApiKeyCreate | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyWithSecret:
    agent = await get_agent_or_404(name, session)
    plain = _generate_key()
    ak = ApiKey(
        agent_id=agent.id,
        key_hash=hash_key(plain),
        label=(payload.label if payload else None),
    )
    session.add(ak)
    await session.commit()
    await session.refresh(ak)
    return ApiKeyWithSecret(
        id=ak.id,
        label=ak.label,
        created_at=ak.created_at,
        revoked_at=ak.revoked_at,
        key=plain,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    name: str, key_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    agent = await get_agent_or_404(name, session)
    result = await session.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.agent_id == agent.id)
    )
    ak = result.scalar_one_or_none()
    if not ak:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Key not found"
        )
    ak.revoked_at = datetime.now(timezone.utc)
    await session.commit()
