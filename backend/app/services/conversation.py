"""
会话服务层：多轮会话的解析、轮次分配、历史裁剪与收尾。

集中承载需求文档 §5.3 / §7 的规则，让 `api/invoke.py` 与 `api/conversations.py`
两个路由保持薄：

- :func:`resolve_conversation` —— 存在性（404）/ 归属（403）/ 过期（410）
- :func:`create_conversation` —— TTL 默认 1 小时、上限 24 小时
- :func:`begin_turn` —— 并发闸门（409）+ 轮次上限（429）+ 落 ``running`` 占位
- :func:`finish_turn` —— 回写 ``succeeded`` / ``failed``
- :func:`load_injected_history` / :func:`build_injected_config` —— 两级裁剪
  （先轮数、再字节），**永不裁剪第 1 轮**

并发模型：当前部署是单 worker uvicorn（`backend/Dockerfile` 的 CMD 未指定
``--workers``，`scripts/start-all.sh` 同样），因此用**进程内闸门**实现
「同一会话并发请求 = 1（其余 409）」；``conversation_turns`` 上的
``UNIQUE(conversation_id, turn_index)`` 作为持久兜底（撞号同样映射 409）。
未来若改多 worker，只需把闸门升级为 DB 行锁/计数列——本文件是唯一改动点。

日志只记标识与计数（conversation_id / turn_index / dropped_turns / 字节数），
**不记** input / output 正文，避免业务数据与个人信息落盘。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.conversation import Conversation, ConversationTurn

log = logging.getLogger(__name__)

# 对外暴露的会话 ID 形状：conv_<32位hex>（需求文档 §4.1 示例 conv_01JQ8Z…）
ID_PREFIX = "conv_"


# ========== ID 编解码 ==========


def format_conversation_id(conversation_id: _uuid.UUID) -> str:
    """内部 UUID → 对外 ID。"""
    return f"{ID_PREFIX}{conversation_id.hex}"


def parse_conversation_id(raw: str) -> _uuid.UUID | None:
    """对外 ID → 内部 UUID。

    容错接受裸 UUID（带/不带连字符）；无法解析返回 None，由调用方映射 404
    ——「不存在」与「格式不对」对调用方是同一件事，无需区分。
    """
    if not isinstance(raw, str):
        return None
    candidate = raw[len(ID_PREFIX):] if raw.startswith(ID_PREFIX) else raw
    try:
        return _uuid.UUID(candidate)
    except (ValueError, AttributeError, TypeError):
        return None


# ========== 进程内并发闸门 ==========


_gates: dict[str, tuple[threading.Lock, int]] = {}
_gates_guard = threading.Lock()


def acquire_conversation_gate(conversation_id: str) -> threading.Lock | None:
    """非阻塞地获取会话闸门；已被占用返回 None（调用方映射 409）。

    用 ``threading.Lock`` 而非 ``asyncio.Lock``：异步调用（invoke/async）的收尾
    发生在后台线程里，锁需要能跨线程释放。非阻塞 acquire 不会让出事件循环。
    """
    with _gates_guard:
        entry = _gates.get(conversation_id)
        if entry is None:
            lock, refs = threading.Lock(), 0
        else:
            lock, refs = entry
        # 引用计数：保证 get() 拿到的锁一定还在注册表里（否则会出现两个并存实例）
        _gates[conversation_id] = (lock, refs + 1)
    if lock.acquire(blocking=False):
        return lock
    _release_gate_ref(conversation_id, lock)
    return None


def release_conversation_gate(conversation_id: str, gate: threading.Lock) -> None:
    """释放会话闸门（幂等；可由其他线程调用）。"""
    if gate.locked():
        try:
            gate.release()
        except RuntimeError:  # pragma: no cover - 理论上不可达，防御性兜底
            log.warning("conversation.gate.release_failed conversation_id=%s", conversation_id)
    _release_gate_ref(conversation_id, gate)


def _release_gate_ref(conversation_id: str, gate: threading.Lock) -> None:
    with _gates_guard:
        entry = _gates.get(conversation_id)
        if entry is None or entry[0] is not gate:
            return
        refs = entry[1] - 1
        if refs <= 0:
            _gates.pop(conversation_id, None)
        else:
            _gates[conversation_id] = (gate, refs)


def reset_gates_for_tests() -> None:
    """仅供测试清理进程内状态。"""
    with _gates_guard:
        _gates.clear()


# ========== 时间处理 ==========


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """MySQL DATETIME 不存时区，读回来是 naive；统一按 UTC 解释。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_expired(conversation: Conversation) -> bool:
    """过期判定（惰性，不依赖后台清理任务）。"""
    return conversation.expires_at is not None and _as_utc(conversation.expires_at) <= _now()


# ========== 生命周期 ==========


async def create_conversation(
    session: AsyncSession,
    *,
    agent_id: _uuid.UUID,
    api_key_id: _uuid.UUID | None,
    metadata: dict | None,
    ttl_seconds: int | None,
    default_ttl_seconds: int,
    max_ttl_seconds: int,
) -> Conversation:
    """新建会话并落库。

    :raises HTTPException: 422 —— ttl_seconds 超过平台上限
    """
    ttl = default_ttl_seconds if ttl_seconds is None else ttl_seconds
    if ttl > max_ttl_seconds:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"ttl_seconds 不能超过 {max_ttl_seconds}",
        )
    conversation = Conversation(
        agent_id=agent_id,
        api_key_id=api_key_id,
        metadata_=metadata or None,
        expires_at=_now() + timedelta(seconds=ttl),
    )
    session.add(conversation)
    await session.commit()
    log.info(
        "conversation.created conversation_id=%s agent_id=%s api_key_id=%s ttl=%s",
        format_conversation_id(conversation.id),
        agent_id,
        api_key_id,
        ttl,
    )
    return conversation


async def resolve_conversation(
    session: AsyncSession,
    conversation_id: str,
    *,
    agent_id: _uuid.UUID,
    api_key_id: _uuid.UUID | None,
) -> Conversation:
    """按对外 ID 解析会话并校验归属与有效期。

    :raises HTTPException: 404 不存在 / 403 归属不符 / 410 已过期
    """
    internal_id = parse_conversation_id(conversation_id)
    conversation = None
    if internal_id is not None:
        result = await session.execute(
            select(Conversation).where(Conversation.id == internal_id)
        )
        conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conversation '{conversation_id}' not found",
        )
    # 归属不符与不存在对外不区分细节，避免泄露他人会话是否存在
    if conversation.agent_id != agent_id or conversation.api_key_id != api_key_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="conversation does not belong to this agent / API key",
        )
    if is_expired(conversation):
        log.info(
            "conversation.expired conversation_id=%s", format_conversation_id(conversation.id)
        )
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"conversation '{conversation_id}' expired, please start a new one",
        )
    return conversation


async def get_conversation_or_403(
    session: AsyncSession,
    conversation_id: _uuid.UUID,
    *,
    agent_id: _uuid.UUID,
    api_key_id: _uuid.UUID | None,
) -> Conversation:
    """按内部 UUID 取会话（CRUD 端点用；语义同 :func:`resolve_conversation`）。"""
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
        )
    if conversation.agent_id != agent_id or conversation.api_key_id != api_key_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="conversation does not belong to this agent / API key",
        )
    return conversation


# ========== 轮次记账 ==========


@dataclass(slots=True)
class TurnAccounting:
    """跨线程的轮次收尾句柄（异步调用用）。

    异步任务在**另一个线程**里执行与收尾，因此不能持有 request session / ORM 对象，
    只带 id 与闸门；收尾时用任务自己的 session 按 id 回写。
    """

    conversation_id: str  # 对外 ID（conv_xxx），用于 webhook 载荷
    turn_index: int
    turn_id: _uuid.UUID
    gate_key: str
    gate: threading.Lock


async def finish_turn_by_id(
    session: AsyncSession,
    accounting: TurnAccounting,
    *,
    succeeded: bool,
    output: dict | None = None,
    trace_id: str | None = None,
) -> None:
    """按 id 回写轮次并**必定**释放并发闸门（异步任务线程用）。"""
    try:
        result = await session.execute(
            select(ConversationTurn).where(ConversationTurn.id == accounting.turn_id)
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            log.warning("conversation.turn.not_found turn_id=%s", accounting.turn_id)
        else:
            await finish_turn(
                session, turn=turn, succeeded=succeeded, output=output, trace_id=trace_id
            )
    except Exception:
        log.exception(
            "conversation.turn.finish_by_id_failed turn_id=%s", accounting.turn_id
        )
    finally:
        release_conversation_gate(accounting.gate_key, accounting.gate)


async def begin_turn(
    session: AsyncSession,
    *,
    conversation: Conversation,
    payload: dict,
    max_turns: int,
) -> ConversationTurn:
    """分配轮次号并落 ``status='running'`` 占位（先写 running 再执行）。

    占位轮次在 agent 执行期间就可见，调用方断线重连后能对齐到具体轮次。

    :raises HTTPException: 429 轮次超上限 / 409 并发撞号（唯一约束兜底）
    """
    result = await session.execute(
        select(func.coalesce(func.max(ConversationTurn.turn_index), 0)).where(
            ConversationTurn.conversation_id == conversation.id
        )
    )
    current_max = int(result.scalar_one() or 0)
    if current_max >= max_turns:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"conversation reached its max turns ({max_turns}), "
                "please start a new one"
            ),
        )

    turn = ConversationTurn(
        conversation_id=conversation.id,
        turn_index=current_max + 1,
        input=payload,
        status="running",
    )
    session.add(turn)
    conversation.updated_at = _now()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # UNIQUE(conversation_id, turn_index) 撞号 = 同会话并发写轮次
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another turn of this conversation is in flight, retry later",
        ) from exc
    log.info(
        "conversation.turn.begin conversation_id=%s turn=%d",
        format_conversation_id(conversation.id),
        turn.turn_index,
    )
    return turn


async def finish_turn(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    conversation: Conversation | None = None,
    succeeded: bool,
    output: dict | None = None,
    trace_id: str | None = None,
) -> None:
    """回写轮次结果（成功带 output，失败只留状态）。"""
    turn.status = "succeeded" if succeeded else "failed"
    turn.output = output if succeeded else None
    turn.trace_id = trace_id
    if conversation is not None:
        conversation.updated_at = _now()
    try:
        await session.commit()
    except Exception:  # pragma: no cover - 收尾失败不能吞掉主流程结果
        await session.rollback()
        log.exception(
            "conversation.turn.finish_failed turn_id=%s", turn.id
        )
        return
    log.info(
        "conversation.turn.finish turn_id=%s status=%s", turn.id, turn.status
    )


# ========== 历史注入与裁剪 ==========


@dataclass(slots=True)
class InjectedHistory:
    """裁剪后的注入历史。"""

    turns: list[dict]
    dropped_turns: int
    truncated: bool
    bytes: int

    @property
    def turn_indexes(self) -> list[int]:
        """本轮实际注入的轮次号（裁剪后可能出现缺号），供平台回报给调用方。"""
        return [turn["index"] for turn in self.turns]


def _turn_to_dict(turn_index: int, input_payload: dict | None, output: dict | None) -> dict:
    return {"index": turn_index, "input": input_payload, "output": output}


def _serialized_size(turn: dict) -> int:
    return len(
        json.dumps(turn, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def trim_history_by_turns(turns: list[dict], max_turns: int) -> list[dict]:
    """第一级裁剪：只保留最近 ``max_turns`` 轮，**永不裁剪第 1 轮**。

    首轮是上下文锚点：当它落在窗口之外时，挤掉窗口内最旧的一轮把它带回来，
    总轮数仍不超过 ``max_turns``。因此注入结果可能存在「缺号」（首轮 + 最近若干轮）。
    """
    if max_turns <= 0:
        return []
    if len(turns) <= max_turns:
        return list(turns)
    has_anchor = turns[0]["index"] == 1
    recent_count = max_turns - 1 if has_anchor else max_turns
    recent = turns[len(turns) - recent_count:] if recent_count > 0 else []
    return ([turns[0]] + recent) if has_anchor else recent


def trim_history_by_bytes(turns: list[dict], max_bytes: int) -> list[dict]:
    """第二级裁剪：超出字节上限时继续丢弃最旧轮次（同样保护第 1 轮）。

    体积按每个轮次对象 ``{"index","input","output"}`` 的紧凑 JSON 字节数计。
    """
    if max_bytes <= 0 or not turns:
        return list(turns)
    sizes = [_serialized_size(turn) for turn in turns]
    total = sum(sizes)
    # 第 1 轮永不裁剪：从下标 1 开始丢；否则从最旧的开始丢
    victim = 1 if turns[0]["index"] == 1 else 0
    while total > max_bytes and victim < len(turns):
        total -= sizes[victim]
        del turns[victim]
        del sizes[victim]
    return turns


async def load_injected_history(
    session: AsyncSession,
    *,
    conversation_id: _uuid.UUID,
    before_turn_index: int,
    max_turns: int,
    max_bytes: int,
) -> InjectedHistory:
    """读取并裁剪待注入的历史。

    注入的只有**已成功**的历史轮次（running / failed 没有可用 output）。
    查询全部走 ``idx_turn_conv``：1 次 COUNT + 1 次窗口读取（首轮锚点落在窗口外时
    再补 1 次按 turn_index=1 的点查），不逐轮查询、不把整表读进来。
    """
    base_filter = (
        ConversationTurn.conversation_id == conversation_id,
        ConversationTurn.turn_index < before_turn_index,
        ConversationTurn.status == "succeeded",
    )
    count_result = await session.execute(
        select(func.count()).select_from(ConversationTurn).where(*base_filter)
    )
    total = int(count_result.scalar_one() or 0)
    if total == 0:
        return InjectedHistory(turns=[], dropped_turns=0, truncated=False, bytes=0)

    rows = await session.execute(
        select(
            ConversationTurn.turn_index, ConversationTurn.input, ConversationTurn.output
        )
        .where(*base_filter)
        .order_by(ConversationTurn.turn_index.desc())
        .limit(max_turns)
    )
    recent = [
        _turn_to_dict(index, payload, output)
        for index, payload, output in rows.all()
    ]
    recent.reverse()

    # 首轮锚点：窗口外还有更旧轮次且窗口内没有第 1 轮时，补一次点查
    if total > len(recent) and recent and recent[0]["index"] != 1:
        anchor_row = await session.execute(
            select(
                ConversationTurn.turn_index,
                ConversationTurn.input,
                ConversationTurn.output,
            ).where(
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.turn_index == 1,
                ConversationTurn.status == "succeeded",
            )
        )
        anchor = anchor_row.first()
        if anchor is not None:
            recent = [_turn_to_dict(anchor[0], anchor[1], anchor[2])] + recent

    kept = trim_history_by_turns(recent, max_turns)
    kept = trim_history_by_bytes(kept, max_bytes)
    dropped = total - len(kept)
    size = sum(_serialized_size(turn) for turn in kept) if kept else 0
    if dropped:
        log.info(
            "conversation.history.truncated conversation_id=%s turn=%d dropped_turns=%d bytes=%d",
            format_conversation_id(conversation_id),
            before_turn_index,
            dropped,
            size,
        )
    return InjectedHistory(
        turns=kept, dropped_turns=dropped, truncated=dropped > 0, bytes=size
    )


def build_injected_config(
    *,
    conversation_id: _uuid.UUID,
    turn_index: int,
    max_turns_injected: int,
    history: InjectedHistory,
) -> dict:
    """组装注入给 agent 的 ``config.conversation``（需求文档 §5.1）。"""
    return {
        "id": format_conversation_id(conversation_id),
        "turn": turn_index,
        "max_turns_injected": max_turns_injected,
        "dropped_turns": history.dropped_turns,
        "truncated": history.truncated,
        "turns": history.turns,
    }


# ========== 能力声明 ==========


async def get_conversation_declaration(
    session: AsyncSession, *, agent_id: _uuid.UUID
) -> dict | None:
    """读取 active 版本的契约里 ``conversation`` 段（仅调用于带会话的请求）。

    :returns: 声明 dict；未声明 / ``enabled`` 非真时返回 None（平台不注入，零开销）
    """
    from ..models.agent_version import AgentVersion

    result = await session.execute(
        select(AgentVersion.contract).where(
            AgentVersion.agent_id == agent_id,
            AgentVersion.is_active.is_(True),
        )
    )
    contract = result.scalars().first()
    if not isinstance(contract, dict):
        return None
    declaration = contract.get("conversation")
    if not isinstance(declaration, dict) or not declaration.get("enabled"):
        return None
    return declaration


def resolve_max_history_turns(declaration: dict | None, default: int, hard_limit: int) -> int:
    """生效的注入轮数上限：契约声明优先，再做一次上限收敛。"""
    if isinstance(declaration, dict):
        declared = declaration.get("max_history_turns")
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
            return min(declared, hard_limit)
    return min(default, hard_limit)
