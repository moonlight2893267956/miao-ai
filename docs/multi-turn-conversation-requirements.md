# 需求文档：平台多轮会话（Conversation）能力

> 状态：草稿待评审
> 影响范围：`backend/app/api/invoke.py`、`backend/app/schemas/`、`backend/app/models/`、agent 运行时协议、前端 Try Run 与调用方 SDK
> 关联：`docs/agent-contract.md`（契约声明）

---

## 1. 背景与问题

平台当前的调用模型是**单轮无状态**：

```python
# agent 侧唯一入口，config 只用于链路追踪
def invoke(input: dict, config: dict = None) -> dict: ...
```

`POST /api/v1/agents/{name}/invoke` 的请求体只有 `{input, metadata}`，每次调用彼此独立。`metadata.session_id` 会被写进 langfuse trace，但它**不是对话轮次**——agent 拿不到任何"上一轮说了什么"。

这导致三类真实场景做不了：

| 场景 | 现状 | 期望 |
|---|---|---|
| 五笔拆字追问「为什么第 2 位是目不是具？」 | 无法回答——agent 不知道在讲哪个字 | 结合上一轮的字与候选作答 |
| 文本对照「这句为什么被改？」 | 同上 | 定位到具体 diff 片段 |
| 连续纠错「不对，应该是土」 | 每轮都从零开始，越改越乱 | 能基于上一轮的判断修正 |

连续调用「问不同的字」（照→牡→们）是**能做的**，它不需要会话——本文档要解决的是「问同一个对象的不同侧面」。

---

## 2. 目标与非目标

### 目标

1. 调用方能创建一个**会话**，在其内连续调用同一 agent，上下文随轮次累积
2. agent 能拿到**结构化的历史**，据此理解「它/第二个/不对，应该是土」这类指代与纠正
3. **完全向后兼容**：不传会话标识时，行为与现在逐字节一致，老 agent 无需任何改动
4. 会话可审计、可清理、可导出

### 非目标（本期不做）

- 平台侧的自动摘要 / token 压缩——只做**裁剪**（保留最近 N 轮），不做智能摘要
- 跨 agent 会话：一个会话只属于一个 agent
- 会话分支树 / 回溯
- 平台内置的「追问意图识别」——那是 agent 的职责，见 §4

---

## 3. 核心决策：状态存在哪里

这是本需求最关键的设计取舍，必须先定，否则实现会跑偏。

| 方案 | 描述 | 评价 |
|---|---|---|
| **A. 平台存历史，agent 无状态** ✅ | 平台保存每轮 input/output；每轮调用把历史注入 `config.conversation`；agent 自己决定怎么用 | ✅ **采纳** |
| B. agent 自己存状态 | 平台只传 `conversation_id`，agent 内部持久化 | ❌ 与 agent 无状态原则冲突；换 agent 丢上下文；无法审计 |

**采纳 A**，理由：

- **与现有架构一致**。五笔 agent 的核心设计是「句子由服务端确定性拼装、模型只做单点判断」，一旦引入会话内可变状态，这条性质就没了
- **可审计、可重放**。任何一次输出都能追溯到完整输入序列
- **换 agent 不丢上下文**。同一会话切到另一个 agent 实现时，历史照样可用
- **实现简单**。平台只需存储 + 注入，不侵入 agent 执行逻辑

代价是每轮都要传历史（带宽与序列化开销）。对五笔这种单轮几 KB 的场景完全可接受；超长会话通过裁剪控制。

---

## 4. API 设计

### 4.1 会话生命周期

```http
POST   /api/v1/agents/{name}/conversations              # 新建
GET    /api/v1/agents/{name}/conversations/{cid}         # 读详情（含全部轮次）
GET    /api/v1/agents/{name}/conversations              # 列表
DELETE /api/v1/agents/{name}/conversations/{cid}         # 删除
```

新建请求：

```json
POST /api/v1/agents/wubi-explainer/conversations
{
  "metadata": { "source": "wubi-toolbox", "user": "anonymous" },
  "ttl_seconds": 3600
}
```

响应（`201`）：

```json
{
  "conversation_id": "conv_01JQ8Z…",
  "agent_name": "wubi-explainer",
  "created_at": "2026-10-11T08:40:00Z",
  "expires_at": "2026-10-11T09:40:00Z",
  "turn_count": 0
}
```

### 4.2 调用时携带会话标识

**不新增端点**，在现有 invoke 请求体的 `metadata` 里加一个字段即可：

```http
POST /api/v1/agents/wubi-explainer/invoke
{
  "input": { "question": "为什么第 2 位是目不是具？" },
  "metadata": { "conversation_id": "conv_01JQ8Z…" }
}
```

`invoke/stream` 与 `invoke/async` 同样支持，语义一致。

> 不复用 `session_id`：它已绑定 langfuse trace 语义，混用会让链路追踪和对话轮次耦合。

### 4.3 响应

```json
{
  "output": { "…": "agent 的结构化输出" },
  "trace_id": "…",
  "conversation_id": "conv_01JQ8Z…",
  "turn": 2,
  "history_truncated": false
}
```

- `turn`：本会话内的第几轮（从 1 开始）
- `history_truncated`：是否因超长做了裁剪，让调用方可感知

新增字段均为**可选**，老调用方忽略即可。

### 4.4 错误语义

| 情况 | HTTP | 说明 |
|---|---|---|
| `conversation_id` 不存在 | `404` | 明确报错，不静默新建 |
| 会话已过期 | `410` | 调用方应新建会话 |
| 会话不属于该 agent | `403` | 防越权 |
| 会话轮次超上限 | `429` | 配额控制，见 §7 |

---

## 5. Agent 侧协议

### 5.1 签名不变，扩展 `config`

```python
def invoke(input: dict, config: dict = None) -> dict: ...
```

平台注入的 `config` 新增：

```python
config = {
    # 现有的链路追踪配置
    "langfuse_session_id": "...", "trace_id": "...",

    # 新增：仅当请求带了 conversation_id 才有
    "conversation": {
        "id": "conv_01JQ8Z…",
        "turn": 2,
        "max_turns_injected": 10,
        "turns": [
            {"index": 1, "input": {...}, "output": {...}},
            ...
        ],
    },
}
```

### 5.2 能力声明（可选）

agent 可通过包根级 `agent.yaml` 声明是否需要历史：

```yaml
conversation:
  enabled: true
  max_history_turns: 10   # 请求读取的最大轮次
```

- `enabled: false`（默认）→ 平台**不注入** `config.conversation`，历史零开销
- `enabled: true` → 平台注入最近 N 轮

这样绝大多数 agent 不受影响，只有真正需要上下文的才付费。

### 5.3 平台侧裁剪规则

按顺序裁剪，保证注入内容始终可用：

1. 轮数超限时，**丢弃最旧的轮次**，保留最近 `max_history_turns` 轮
2. 仍超字节上限（默认 32KB）时，继续丢弃旧轮次
3. 裁剪发生时置 `history_truncated: true`，并在 `config.conversation` 里标记 `dropped_turns`
4. **永不裁剪第 1 轮**——它是上下文的锚点

---

## 6. 数据模型

```sql
CREATE TABLE conversations (
    id                UUID PRIMARY KEY,
    agent_id          UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    api_key_id        UUID REFERENCES api_keys(id) ON DELETE SET NULL,  -- 归属，鉴权用
    metadata          JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conv_agent_created ON conversations(agent_id, created_at DESC);

CREATE TABLE conversation_turns (
    id                UUID PRIMARY KEY,
    conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    turn_index        INT  NOT NULL,          -- 从 1 开始
    input             JSONB NOT NULL,
    output            JSONB,
    status            VARCHAR(16) NOT NULL,   -- running | succeeded | failed
    trace_id          VARCHAR(64),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, turn_index)
);
CREATE INDEX idx_turn_conv ON conversation_turns(conversation_id, turn_index);
```

要点：

- **`api_key_id` 用于隔离**：会话归属到调用方，A 的 key 读不到 B 的会话
- **轮次号唯一约束**：防止并发写入撞号
- **先写 `running` 再执行**：调用方在 agent 执行期间就能查到占位轮次，便于断线重连后对齐

---

## 7. 约束与配额

| 项 | 默认 | 可配 |
|---|---|---|
| 会话最大轮次 | 50 | 是 |
| 注入历史的最大轮次 | 10 | agent 侧 `max_history_turns` |
| 注入历史字节上限 | 32 KB | 是 |
| 会话 TTL | 1 小时 | 新建时指定，上限 24 小时 |
| 同一会话并发请求 | **1**（其余返回 409） | 否 |

并发限制的理由：同一会话的两轮并发会让 `turn_index` 顺序失真，而上下文对顺序敏感。

---

## 8. 前端与调用方改造

### 8.1 平台 Try Run 面板

- 顶部加一个**会话指示器**：「新会话」/ 会话 ID + 轮次计数 + 「重置」按钮
- 不点重置时，Run 会自动带上当前 `conversation_id`
- 输出区改为**轮次列表**（最新在上），每轮显示 input 摘要 + output
- 流式（`invoke/stream`）下同样按轮次追加

### 8.2 调用方（miao-toolbox）

```java
// 伪代码
var convId = aiClient.startConversation("wubi-explainer");
var r1 = aiClient.invoke("wubi-explainer", Map.of("char", "看"), convId);
var r2 = aiClient.invoke("wubi-explainer",
        Map.of("question", "为什么第 2 位是目不是具？"), convId);
```

---

## 9. 验收标准

以五笔场景为验收载体：

**功能**

1. 新建会话 → 问「看」→ 追问「为什么第 2 位是目不是具？」→ 回答中能正确引用上一轮的字与候选，**不编造**
2. 追问「不对，第 3 位应该是土」→ agent 能基于上一轮重新校验并修正
3. 连续问 3 个不同的字，会话正常工作且**不会把上下文串味**（问第 2 个字时不应提到第 1 个字）

**兼容性（硬要求）**

4. 不传 `conversation_id` 调用，所有既有 agent（含未声明 `conversation.enabled` 的）行为**完全不变**
5. 现有 14 个 API key 的存量调用方无需任何代码修改
6. `invoke/stream` 与 `invoke/async` 在带会话时同样工作，轮次记录不丢

**工程**

7. 会话历史的读写不使 P99 延迟劣化超过 50 ms
8. 注入历史的体积受 §7 上限约束，超限按规则裁剪且可观测

---

## 10. 分期建议

| 阶段 | 内容 | 产出 |
|---|---|---|
| P0 | 数据模型 + 会话 CRUD 端点 | 会话能建能查，但 agent 还没用 |
| P1 | invoke 注入 `config.conversation` + agent.yaml 能力声明 | 五笔 agent 开启后即可追问 |
| P2 | 五笔 agent 实现追问处理 | 验收用例 1–3 通过 |
| P3 | 前端 Try Run 轮次 UI + toolbox 接入 | 用户可见 |

---

## 11. 风险与取舍

| 风险 | 应对 |
|---|---|
| 历史注入增大 prompt，token 成本上升 | 裁剪 + 上限；agent 可声明 `max_history_turns` |
| agent 实现质量参差，导致「拿着上下文也答不好」 | 能力声明为 opt-in；不声明则零影响；用五笔场景做样板 |
| 会话表膨胀 | TTL 自动过期 + 按 agent 清理；轮次随会话级联删除 |
| 并发写轮次撞号 | 唯一约束 + 并发限制（§7） |

**最大的判断风险**：多轮会让 agent 行为变得依赖历史，**降低可复现性**。缓解手段是——历史注入完全由平台完成、agent 输入因此是确定的（同会话同历史必得同结果，除非模型本身有随机性），且每轮的完整输入都落库可复现。
