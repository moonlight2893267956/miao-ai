# Task: Implement LLM Model Management for Miao AI

## Overview
Add LLM Provider & Model management to the Miao AI backend + frontend. This enables per-agent model configuration instead of hardcoded DashScope env vars.

## Architecture Summary
- **Backend**: FastAPI + SQLAlchemy (async) + PostgreSQL (Neon)
- **Frontend**: Next.js 14 (App Router) + shadcn/ui + Tailwind + lucide-react
- **Runtime**: Agent subprocess (venv/Docker) managed by ManagedAgent dataclass
- **DB**: UUID PKs, `Mapped[T]` + `mapped_column()` pattern, `from_attributes=True` schemas

## What to Build (10 Steps)

### Step 1: Encryption Utility
**New file**: `backend/app/crypto.py`
```python
"""Fernet-based encryption for sensitive fields (e.g., LLM provider API keys)."""
import os
from cryptography.fernet import Fernet

def _get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY not set in environment")
    return Fernet(key.encode())

def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
```

**Modify**: `backend/app/config.py` — add field in Settings class:
```python
# ===== Security =====
encryption_key: str = ""  # ENCRYPTION_KEY from .env
```

**Modify**: `.env.example` — add:
```
# ===== Encryption (Fernet key for API key encryption) =====
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=
```

### Step 2: DB Models

**New file**: `backend/app/models/model_provider.py`
```python
"""LLM Provider: e.g., DashScope, OpenAI, DeepSeek."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .llm_model import LlmModel


class ModelProvider(Base):
    __tablename__ = "model_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    models: Mapped[list["LlmModel"]] = relationship(
        "LlmModel", back_populates="provider", cascade="all, delete-orphan"
    )
```

**New file**: `backend/app/models/llm_model.py`
```python
"""LLM Model definition under a provider."""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .model_provider import ModelProvider


class LlmModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, server_default="4096", nullable=False)
    temperature_default: Mapped[float] = mapped_column(
        Float, server_default="0.7", nullable=False
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    provider: Mapped["ModelProvider"] = relationship("ModelProvider", back_populates="models")
```

**Modify**: `backend/app/models/agent.py` — add after `description` field:
```python
from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID

# In class Agent, add:
model_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("llm_models.id", ondelete="SET NULL"),
    nullable=True, index=True,
)
```

**Modify**: `backend/app/models/__init__.py` — add imports:
```python
from .model_provider import ModelProvider
from .llm_model import LlmModel
# Add to __all__
```

### Step 3: Alembic Migration

**New file**: `backend/alembic/versions/b5d8e2a91034_add_model_providers_and_llm_models.py`

Migration: create `model_providers` table, create `llm_models` table, add `model_id` column to `agents`.

downgrade: drop `model_id` from agents, drop `llm_models`, drop `model_providers`.

Revision ID: `b5d8e2a91034`, Revises: `a3c5e7f90123`

### Step 4: Pydantic Schemas

**New file**: `backend/app/schemas/model_provider.py`
- ProviderCreate(name, api_key, base_url) — api_key is plaintext input, encrypt before DB storage
- ProviderUpdate(name?, api_key?, base_url?) — if api_key provided, re-encrypt
- ProviderRead(id, name, base_url, created_at) — NEVER expose api_key

**New file**: `backend/app/schemas/llm_model.py`
- LlmModelCreate(name, provider_id, model_id, max_tokens=4096, temperature_default=0.7, is_default=False)
- LlmModelUpdate(name?, model_id?, max_tokens?, temperature_default?, is_default?)
- LlmModelRead(id, name, provider_id, model_id, max_tokens, temperature_default, is_default, created_at, provider_name=None)

**Modify**: `backend/app/schemas/agent.py` — add `model_id: uuid.UUID | None = None` to AgentCreate and AgentRead

### Step 5: API Endpoints

**New file**: `backend/app/api/providers.py` — prefix `/providers`
- GET / — list all providers
- POST / — create (encrypt api_key with crypto.encrypt())
- PUT /{id} — update (if api_key in body, re-encrypt)
- DELETE /{id} — delete (cascade to models)

**New file**: `backend/app/api/models.py` — prefix `/models`
- GET / — list all models with provider_name (join query)
- POST / — create (if is_default=True, clear other defaults first)
- PUT /{id} — update
- DELETE /{id} — delete
- POST /{id}/set-default — set as system default

**Modify**: `backend/app/api/agents.py` — add endpoint:
```python
class AgentModelUpdate(BaseModel):
    model_id: uuid.UUID | None  # None = use system default

@router.put("/{name}/model", response_model=AgentRead)
async def update_agent_model(name: str, payload: AgentModelUpdate, session=Depends(get_session)):
    agent = await get_agent_or_404(name, session)
    agent.model_id = payload.model_id
    await session.commit()
    await session.refresh(agent)
    return await _with_status(agent, AgentRegistry.instance(), session)
```

**Modify**: `backend/app/main.py` — register new routers:
```python
from .api.providers import router as providers_router
from .api.models import router as models_router
app.include_router(providers_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
```

### Step 6: LLM Env Var Injection (Core Logic)

**New file**: `backend/app/runtime/llm_env.py`
```python
"""Resolve LLM environment variables for agent subprocess/container."""
import os
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..models.agent import Agent
from ..models.llm_model import LlmModel
from ..models.model_provider import ModelProvider
from ..crypto import decrypt


async def resolve_llm_env(agent_id: uuid.UUID, session: AsyncSession) -> dict[str, str]:
    """Resolve LLM_* env vars for an agent.
    
    Priority: agent-specific model > system default model > .env DASHSCOPE_*
    Returns dict with LLM_API_KEY, LLM_BASE_URL, LLM_MODEL + DASHSCOPE_* aliases.
    """
    # Try agent-specific model
    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    
    model = None
    provider = None
    
    if agent and agent.model_id:
        r = await session.execute(select(LlmModel).where(LlmModel.id == agent.model_id))
        model = r.scalar_one_or_none()
    
    # Fallback to system default
    if not model:
        r = await session.execute(select(LlmModel).where(LlmModel.is_default.is_(True)).limit(1))
        model = r.scalar_one_or_none()
    
    if model:
        r = await session.execute(select(ModelProvider).where(ModelProvider.id == model.provider_id))
        provider = r.scalar_one_or_none()
    
    if model and provider:
        api_key = decrypt(provider.api_key_encrypted)
        env = {
            "LLM_API_KEY": api_key,
            "LLM_BASE_URL": provider.base_url,
            "LLM_MODEL": model.model_id,
            "DASHSCOPE_API_KEY": api_key,
            "DASHSCOPE_BASE_URL": provider.base_url,
            "DASHSCOPE_MODEL": model.model_id,
        }
    else:
        env = {
            "LLM_API_KEY": os.environ.get("DASHSCOPE_API_KEY", ""),
            "LLM_BASE_URL": os.environ.get("DASHSCOPE_BASE_URL", ""),
            "LLM_MODEL": os.environ.get("DASHSCOPE_MODEL", "qwen-plus"),
            "DASHSCOPE_API_KEY": os.environ.get("DASHSCOPE_API_KEY", ""),
            "DASHSCOPE_BASE_URL": os.environ.get("DASHSCOPE_BASE_URL", ""),
            "DASHSCOPE_MODEL": os.environ.get("DASHSCOPE_MODEL", "qwen-plus"),
        }
    return env
```

**Modify**: `backend/app/runtime/process.py` — add `extra_env` param:
```python
def spawn_agent_process(
    venv_python: str, runner_path: Path, agent_dir: Path,
    entrypoint: str, port: int, log_path: Path,
    extra_env: dict[str, str] | None = None,  # NEW
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "ab")
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [venv_python, str(runner_path), str(agent_dir), entrypoint, str(port)],
        stdout=log_file, stderr=subprocess.STDOUT, env=env, start_new_session=True,
    )
```

**Modify**: `backend/app/runtime/manager.py` — add `llm_env` field to ManagedAgent:
```python
@dataclass
class ManagedAgent:
    # ... existing fields ...
    llm_env: dict[str, str] = field(default_factory=dict)  # NEW
```
- In `_start_process()`: pass `extra_env=self.llm_env` to `spawn_agent_process()`
- In `_build_and_start_docker()`: add `self.llm_env` to env_vars dict

**Modify**: `backend/app/api/versions.py` — in `activate_version`, before creating ManagedAgent:
```python
from ..runtime.llm_env import resolve_llm_env
llm_env = await resolve_llm_env(agent.id, session)
# Pass llm_env to ManagedAgent(...)
```

**Modify**: `backend/app/main.py` — in `_recover_active_agents`, before creating ManagedAgent:
```python
from .runtime.llm_env import resolve_llm_env
# Inside the for loop, get a session and resolve:
async with AsyncSessionLocal() as s:
    llm_env = await resolve_llm_env(agent.id, s)
# Pass llm_env=llm_env to ManagedAgent(...)
```

**Modify**: `backend/app/api/invoke.py` — in `_try_auto_activate`, similarly resolve and pass llm_env

### Step 7: Update Sample Agent

**Modify**: `demos/sample-agent/agent.py` — change ChatOpenAI init to:
```python
_llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL") or os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
    temperature=0,
    api_key=os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL"),
)
```

### Step 8: Frontend — TypeScript Types & API Client

**Modify**: `frontend/src/lib/api.ts` — add types and functions:

```typescript
export type ModelProvider = {
  id: string; name: string; base_url: string; created_at: string;
};

export type LlmModel = {
  id: string; name: string; provider_id: string; provider_name: string | null;
  model_id: string; max_tokens: number; temperature_default: number;
  is_default: boolean; created_at: string;
};

// Add to Agent type: model_id: string | null;

// API functions:
export const listProviders = () => http<ModelProvider[]>("/api/v1/providers");
export const createProvider = (data: { name: string; api_key: string; base_url: string }) =>
  http<ModelProvider>("/api/v1/providers", { method: "POST", json: data });
export const updateProvider = (id: string, data: { name?: string; api_key?: string; base_url?: string }) =>
  http<ModelProvider>(`/api/v1/providers/${id}`, { method: "PUT", json: data });
export const deleteProvider = (id: string) =>
  http<void>(`/api/v1/providers/${id}`, { method: "DELETE" });

export const listModels = () => http<LlmModel[]>("/api/v1/models");
export const createModel = (data: { name: string; provider_id: string; model_id: string; max_tokens?: number; temperature_default?: number; is_default?: boolean }) =>
  http<LlmModel>("/api/v1/models", { method: "POST", json: data });
export const updateModel = (id: string, data: { name?: string; model_id?: string; max_tokens?: number; temperature_default?: number; is_default?: boolean }) =>
  http<LlmModel>(`/api/v1/models/${id}`, { method: "PUT", json: data });
export const deleteModel = (id: string) =>
  http<void>(`/api/v1/models/${id}`, { method: "DELETE" });
export const setDefaultModel = (id: string) =>
  http<LlmModel>(`/api/v1/models/${id}/set-default`, { method: "POST" });

export const updateAgentModel = (name: string, model_id: string | null) =>
  http<Agent>(`/api/v1/agents/${name}/model`, { method: "PUT", json: { model_id } });
```

### Step 9: Frontend — Models Management Page

**New file**: `frontend/src/app/models/page.tsx`

Create a "use client" page with:
- Two sections: Providers (cards) and Models (table/list)
- Provider cards show name + base_url + edit/delete buttons
- Provider create/edit dialog with name, api_key (password input), base_url fields
- Model list shows name, model_id, provider_name, max_tokens, temperature, is_default badge, actions
- Model create/edit dialog with name, provider dropdown, model_id, max_tokens, temperature_default
- Set-default and delete buttons per model row
- Use existing shadcn/ui Button, Input, Label components + lucide-react icons

**Modify**: `frontend/src/components/layout/sidebar.tsx` — add nav item:
```typescript
import { Cpu } from "lucide-react";
// Add to NAV_ITEMS:
{ label: "模型管理", items: [{ href: "/models", label: "Models", icon: Cpu }] },
```

### Step 10: Frontend — Agent Detail Model Selector

**Modify**: `frontend/src/app/agents/[name]/page.tsx`
- In the agent header area, add a model selector dropdown
- Options: "System Default" + all models formatted as "Provider / Model Name"
- Selecting calls `updateAgentModel(name, model_id)` or `updateAgentModel(name, null)` for default

## Important Notes
- `cryptography` package is already installed in backend venv
- Follow existing code patterns exactly (see existing files for reference)
- All Python files must pass `py_compile` syntax check
- The alembic migration file is handwritten (autogenerate doesn't work with this setup)
- Current head revision is `a3c5e7f90123`
- DB connection uses `from ..db import AsyncSessionLocal, get_session`
- Use `from ..utils import get_agent_or_404` for agent lookups
- ProviderRead must NEVER expose api_key in any form
