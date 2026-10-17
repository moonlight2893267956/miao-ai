# Agent SDK：执行事件上报（`event: exec`）

> 状态：**已实现**（平台通用能力，任何 agent 可用）
> 实现：`backend/agent_templates/miao_sdk.py`（SDK）· `backend/agent_templates/miao_runner.py`（协议）
> 首个消费者：`demos/wubi-explainer`

## 1. 用来解决什么

平台的 `/invoke` 是「跑完才回」的，而真实 agent 里慢的是**外部调用**：联网检索
（五笔 agent 实测单次 4~7s）、模型推理（单次上限 70s）、第三方 API。这段时间
调用方只能转圈，既不知道在等什么，也看不出卡在哪一步。

**执行事件上报**把 agent 执行过程中发生的事**原样**抛给上游：调了哪些工具、
入参出参是什么、耗时多久、成功还是失败。平台**只做管道**，不解释、不裁剪语义 ——
怎么展示、要不要展示、怎么分组，是上游自己的事。

这一点和 coding agent（Claude Code / Cursor 那一类）一致：用户能看到
「调用某工具 → 拿到结果 → 再继续」，而不是只看到一个最终答案。

**它不是什么**：不是 LLM function calling 的 `tool_calls` 透出。这里上报的是
agent 内部**确定性的外部调用**，与模型是否支持工具调用无关。

## 2. 设计对齐（为什么长这样）

事件模型对齐 OpenAI Responses API / Anthropic Messages 的流式事件，两条原则：

1. **配对靠 ID，不靠状态**。工具调用是 `tool_call{call_id, name, input}` +
   后续 `tool_result{call_id, ...}` —— 上游用 `call_id` 关联，而不是让调用事件
   自己带一个「done」。这样**流式增量**以后也能加（`tool_call` 多次追加同 `call_id`
   的片段），协议不用改。
2. **平台只认一个字段**：`kind`。其余字段一律原样透传 —— 这样任何 agent 的
   任何自定义事件类型都能走同一条管道，平台不需要为每种 agent 加分支。

与 OpenAI 的两处**有意偏离**（因为我们是服务端确定性调用，不是模型生成）：

| OpenAI | 这里 | 原因 |
|---|---|---|
| `arguments` 是字符串化 JSON | `input` / `output` 是**对象** | 服务端调用不需要流式拼接参数，对象更省一次解析、也更好读 |
| 拆成 `*.delta` / `*.done` 两套事件 | 一次 `tool_call` + 一次 `tool_result` | 当前不需要增量；需要时可加，`call_id` 已预留配对语义 |

## 3. 快速接入（任何 agent 三步）

**第一步：把外部调用包成工具**

```python
import miao_sdk

with miao_sdk.tool("tavily.search", query="编 五笔编码") as t:
    data = tavily.search("编")
    t.output({"results": len(data)})        # 出参：对象
```

`tool()` 自动做三件容易漏的事：**分配 `call_id`**、**计时**、**异常也成对收尾**
（异常先记成 `ok: false` 的 result，然后**原样抛出** —— SDK 不吞业务异常，是否降级由 agent 决定）。

**第二步：流式入口交给 SDK 的 `stream()`**

```python
def invoke(input, config=None):
    if (config or {}).get("stream"):
        return miao_sdk.stream(lambda: _dispatch(input, config),
                               render=lambda r: _pieces(r.get("explanation")))
    return _dispatch(input, config)      # 非流式：没注册 sink，上报是空操作
```

`stream()` 负责**把同步流水线放到后台线程**，这是整套能力的关键 ——
`_dispatch` 里的联网与模型调用都是阻塞的，只有挪到后台，主线程才能在阻塞
**进行中**把事件交出去。自己起线程也行（用 `use_sink`），但没必要。

**第三步：确认打包方式**（通常不需要做任何事）

SDK 由平台放在 **runner 同目录**：

| 运行时 | SDK 位置 | 为什么 agent 能 import 到 |
|---|---|---|
| venv 子进程 | `backend/agent_templates/miao_sdk.py` | 脚本模式下 `sys.path[0]` 就是 runner 所在目录 |
| Docker | 镜像 `/app/miao_sdk.py` | Dockerfile 第一层 `COPY miao_runner.py miao_sdk.py /app/` |

所以 agent **不需要**把 SDK 打进自己的 zip，也不需要写进 `requirements.txt`。
缺少它时 runner 会打印一行 warning 并照常启动，agent 侧的上报静默降级为空操作
——「功能」绝不为「可观测」让路。

## 4. 事件契约

### 4.1 agent → runner：第四种流式块

与 `{"token": ...}` / `{"output": ...}` 并列：

```python
{"exec": {"kind": "tool_call", "call_id": "c1",
          "name": "tavily.search", "input": {"query": "编 五笔编码"}}}
```

### 4.2 runner → 调用方：`event: exec`

```text
event: exec
data: {"seq":1,"kind":"tool_call","call_id":"c1","name":"tavily.search","input":{"query":"编 五笔编码"}}

event: exec
data: {"seq":2,"kind":"tool_result","call_id":"c1","name":"tavily.search","ok":true,"ms":4271,"output":{"results":5}}
```

最终结果仍然走 `event: done` 的 `done.output`（结构化结果一次性给出）。

### 4.3 字段

| 字段 | 出现在 | 说明 |
|---|---|---|
| `kind` | 全部 | 事件类型，**由 agent 决定**；平台只用它归类，不校验取值 |
| `seq` | 全部 | SDK 自动加的**自增序号**（每条流内从 1 开始单调），上游据此发现缺口 |
| `text` | `thinking` / `log` | 文本内容 |
| `level` | `log` | `info` / `warn` / `error`（自由取值，平台不校验） |
| `call_id` | `tool_call` / `tool_result` | **配对键**：一次调用的 call 与 result用它关联 |
| `name` | `tool_call` / `tool_result` | 工具名（自由取值，建议 `域.动作`） |
| `input` | `tool_call` | 工具入参（**对象**） |
| `ok` | `tool_result` | 是否成功 |
| `output` / `error` | `tool_result` | 出参（对象）/ 失败原因 |
| `ms` | `tool_result` | 耗时（毫秒，SDK 自动算） |
| `truncated` | 任意 | 该字段被体积上限截断时出现的标记 |

SDK 导出的推荐事件类型（**只是推荐**，可以完全不用、也可以自定义任意 `kind`）：

```python
miao_sdk.EVENT_KINDS     # ("thinking", "tool_call", "tool_result", "log")
miao_sdk.emit({"kind": "retrieval", "hits": [...]})   # 逃生通道：自定义事件
```

### 4.4 语义边界

- `thinking` 是**模型内容**，可能有错、可能不完整。**没有就不发，不要编。**
- `tool_call` / `tool_result` 是**执行事实**，由代码产生。
- `log` 是 agent 想说的执行说明（降级、跳过、重试原因）。
- 上游应把 `thinking` 与执行事实**分开呈现**，不要把 thinking 当事实引用。

## 5. 纪律（SDK 已内建）

| 纪律 | 说明 |
|---|---|
| **永不抛异常** | sink 抛错被吞掉；上报失败绝不能拖垮主流程 |
| **无 sink 时零开销** | 非流式路径所有上报立即返回，不做任何分配 |
| **线程隔离** | sink 存 `threading.local()`，并发 invoke 各一份，互不串味 |
| **每条流重置** | `seq` / `call_id` 每条流从 1 开始（`clear()` 一并重置计数器） |
| **体积上限** | 文本 2KB / 对象 4KB / 错误 500B，超限**显式标记** `truncated`（悄悄截断比截断更危险） |
| **事件是辅助信息** | 上游丢弃全部事件也必须能正常拿到最终结构化结果；不参与任何校验门槛 |

`stream()` 额外保证：`work` 抛异常时归一成带 `error` 的 dict（平台对 agent 的
「绝不向外抛异常」纪律由 SDK 兜住），收尾块一定到达（`finally`）。

## 6. 平台侧行为

| 层 | 行为 |
|---|---|
| agent | 上报 `{"exec": {...}}` 块 |
| `miao_runner` | 转成 `event: exec`；**不当 token、不计入 `done.output`**；最近 ≤200 条记进 Langfuse span 的 `metadata.exec_events` 供复盘 |
| `miao_runner`（兜底） | 若 agent **手写 generator** 而非用 `stream()`，`tool()` 的事件会进 sink —— runner 用 `_EventRelay` 接住并插进流里，保证「agent 怎么写都能出来」 |
| 每轮 invoke 开始 | 主动清 SDK 状态（sink + 计数器），防上一轮残留 |
| 平台 `/invoke/stream` | 对未知事件名全量透传（`yield line`），因此 `exec` 零改动即可通过；已有测试锁住这条契约 |
| 会话记账 | 该轮 `output` 仍只取自 `done.output` / `output` 事件；执行事件**不会**被写进轮次记录 |
| Try Run | 识别 `event: exec` 并渲染「执行过程」（工具调用按 `call_id` 折叠成一行、失败/重试如实显示），不再拼进打字机正文 |

## 7. 降级与排障

**SDK 缺失**（旧镜像、手工部署漏拷 `miao_sdk.py`）：runner 启动时打印

```
[miao_runner] miao_sdk SDK 不可用，执行事件上报将静默降级：No module named 'miao_sdk'
```

agent 照常工作，只是上游看不到过程。排查顺序：

1. venv 模式：确认 `backend/agent_templates/miao_sdk.py` 存在；
2. Docker 模式：确认镜像里 `/app/miao_sdk.py` 存在（由 `sync_runtime_files()` 拷进构建上下文）；
3. 改了 SDK 之后**必须重新激活版本**才会生效（与 runner 同理）。

**看不到过程但要确认上报有没有发生**：查该次调用的 Langfuse trace，
`agent:invoke/stream` span 的 `metadata.exec_events` 就是本次上报的全部事件。

**客户端侧断连**：流中断后 agent 的后台线程是 daemon，跑完自行退出，不阻塞进程。

## 8. 测试

```bash
cd backend
# SDK 契约本身（配对 / 计时 / 线程隔离 / 体积上限 / 时序）
.venv/bin/python -m pytest tests/test_agent_sdk_exec.py -q
# runner 的三种块协议（含老 agent 零回归、手写 generator、SDK 缺失兜底）
.venv/bin/python -m pytest tests/test_runner_stream_exec.py -q
# 平台透传 + 不污染轮次 output
.venv/bin/python -m pytest tests/test_invoke_exec_passthrough.py -q
```

这些测试**都不依赖任何具体 agent**（临时生成 fixture agent），测的是平台协议本身。

消费方自检范例见 `demos/wubi-explainer/evals/check_exec_events.py`：
离线验证「工具调用成对、入参出参来自真实执行、阻塞期间先到、失败如实上报、
非流式零事件、事件不引用模型文本」。

## 9. 兼容性与演进

- 新增事件类型（`event: xxx`）是**纯加法**：老 agent、老客户端、老调用方都不受影响。
- **`kind` 永远不设白名单**：一旦平台开始校验取值域，「通用能力」就退化成
  「只服务某些 agent 的定制功能」。
- 若要给**事件名**加白名单，必须同步更新本文与 `test_invoke_exec_passthrough.py`
  —— 否则执行事件会静默断掉，线上只表现为「上游看不到工具调用」。
- 字段只增不改：上游必须容忍未知字段、未知 `kind`（降级为通用条目）。
