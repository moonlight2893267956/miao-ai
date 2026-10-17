# Agent 执行事件流 · 上游对接文档

> 版本：v1.0 ｜ 2026-10-17
> 适用：任何通过 HTTP 调用 Miao AI Agent 的上游服务 / 客户端
> 范围：如何消费 **SSE 流式调用**，尤其是如何拿到并展示 agent 的**执行过程**（工具调用）

---

## 1. 这份文档解决什么

你调用一个 Agent 时，它内部往往在跑**耗时的外部动作**：联网检索、模型推理、查库。
在改造前，这几十秒里你只能看到连接处于等待状态，不知道它在做什么、卡在哪一步。

现在平台会把 agent 执行过程中的事件**实时**推给你，形态与 coding agent
（Claude Code / Cursor 那类）一致：你能看到「调用了哪个工具、入参是什么、
拿到什么结果、花了几秒、成功还是失败」。

**平台只做管道**：事件内容由 agent 决定，平台原样透传，不解释语义。
如何展示、要不要展示、怎么分组，完全由你（上游）决定。

**两条硬规则**：

1. **执行事件是辅助信息**。你即使一条都不处理、全部丢弃，也照样能从
   `done` 事件里拿到完整、可信的结构化结果。
2. **最终结果永远走 `done.output`**。执行事件里出现的工具出参**不是**最终答案，
   不要用它替代 `done.output` 做业务判断。

---

## 2. 端点

| 项 | 值 |
|---|---|
| 方法 | `POST` |
| 路径 | `{BASE_URL}/api/v1/agents/{agent_name}/invoke/stream` |
| 鉴权 | `Authorization: Bearer <api_key>`（per-agent 的 API Key，由平台方提供） |
| 请求头 | `Content-Type: application/json`、`Accept: text/event-stream` |
| 响应 | `200`，`Content-Type: text/event-stream; charset=utf-8`，分块传输 |

> 另有同步端点 `POST .../invoke`（一次性返回 JSON），它**不含任何执行事件**，
> 行为与本能力上线前完全一致。需要"看过程"就必须用 `/invoke/stream`。

### 2.1 响应头（体现为流式、且不可被代理缓冲）

```
HTTP/1.1 200 OK
content-type: text/event-stream; charset=utf-8
cache-control: no-cache
x-accel-buffering: no
transfer-encoding: chunked
```

> 若你在前面还有一层反向代理（nginx 等），务必关闭对下游响应的缓冲
> （`proxy_buffering off;`），否则事件会被攒到你关闭连接时才一次性吐出。

---

## 3. 请求体

```json
{
  "input": { "...": "该 agent 自己的入参契约" },
  "metadata": { "conversation_id": "可选的会话 ID" }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `input` | ✅ | agent 的业务入参，形状由该 agent 的**契约**决定，见 §9 |
| `metadata.conversation_id` | 否 | 传入则本轮进入多轮会话（历史由平台注入），详见 §8 |

---

## 4. 事件总览

一条流由若干 `event:` + `data:` 组成，**每个事件以空行结束**（标准 SSE）：

| `event` 名 | 何时出现 | 你要不要处理 |
|---|---|---|
| `exec` | agent 上报执行过程（**本文重点**） | 建议处理；不处理请**忽略** |
| `token` | 最终答案的正文分片 | 建议处理（用于打字机效果） |
| `done` | 流正常结束 | **必须处理**：取最终结果与 trace |
| `error` | agent 或转发出错 | **必须处理**：本轮失败 |
| `conversation` | 仅带 `conversation_id` 时，流的最前面补发一条 | 建议处理（拿轮次号） |

### 4.1 关键提醒：未知事件必须忽略

平台未来可能新增事件类型。**你的解析器必须默认忽略不认识的事件名**，
而不是把它拼进正文、也不能因此中断流。这是平台与上游之间的约定。

---

## 5. `exec` 事件（重点）

### 5.1 原始报文

```
event: exec
data: {"kind":"tool_call","call_id":"c1","name":"tables.lookup","input":{"char":"编"},"seq":1}

event: exec
data: {"kind":"tool_result","call_id":"c1","name":"tables.lookup","ok":true,"output":{"hit":true,"code":"xyna"},"ms":0,"seq":2}
```

### 5.2 字段

| 字段 | 类型 | 出现在 | 说明 |
|---|---|---|---|
| `kind` | string | 全部 | 事件类型。**不设白名单**，agent 可自定义（见 §5.3） |
| `seq` | int | 全部 | 单调递增序号，每条流从 1 开始。可用它发现丢包/乱序 |
| `text` | string | `thinking` / `log` | 文本内容 |
| `level` | string | `log` | `info` / `warn` / `error`（自由取值，**不要枚举校验**） |
| `call_id` | string | `tool_call` / `tool_result` | **配对键**：一次调用的开始与结束用它关联 |
| `name` | string | `tool_call` / `tool_result` | 工具名，形如 `域.动作`（如 `tavily.search`） |
| `input` | object | `tool_call` | 工具入参（**JSON 对象，不是字符串**） |
| `ok` | bool | `tool_result` | 是否成功。**可能缺失**，缺失时按"未知"处理 |
| `output` | object | `tool_result` | 工具出参（JSON 对象） |
| `error` | string | `tool_result` | 失败原因（仅失败时） |
| `ms` | int | `tool_result` | 耗时（毫秒） |
| `truncated` | bool | 任意 | 该字段因体积上限被截断。**出现即表示看到的内容不完整** |

### 5.3 内置的 `kind`（推荐处理，但不要写死）

| `kind` | 含义 | 语义级别 |
|---|---|---|
| `tool_call` | 开始调用一个工具 | **执行事实**（由代码产生） |
| `tool_result` | 工具调用结束 | **执行事实** |
| `log` | agent 的执行说明（降级、跳过、重试原因） | 执行事实 |
| `thinking` | 模型的思考内容 | ⚠️ **模型内容**，可能有错、可能不完整 |

> **`thinking` 与执行事实必须分开呈现。** 它可能包含模型的错误猜测；
> 把它与 `tool_result` 混在一起展示，会使用户误以为那是可核验的事实。

### 5.4 为什么是「两条事件」而不是一条

一次工具调用由 `tool_call` + `tool_result` **两条事件**组成，靠 `call_id` 配对。
这样设计的原因：调用**开始**的时刻就能告诉你「现在在做什么」（这正是等待期最需要的），
而结果往往几秒后才到。**所以你应当先渲染"进行中"，收到 result 后再更新为完成态。**

未等到 `tool_result` 的调用，就应显示为「进行中」——这也是等待期唯一真实的进度信息。

### 5.5 时间线实例（真实一次「编」字的调用，离线模式）

```
t=0.00s  exec  tool_call    tables.lookup   {"char":"编"}
t=0.00s  exec  tool_result  tables.lookup   ok  0ms   {"hit":true,"code":"xyna"}
t=0.00s  exec  log(info)    "调用方要求离线，跳过联网核验"
t=0.00s  exec  tool_call    llm.decide      {"model":"deepseek-v4-flash-0731","attempt":1}
t=3.88s  exec  tool_result  llm.decide      ok  3877ms  {"chars":570,"parsed":true}
t=3.88s  token "「编」的五笔" …（正文打字机）
t=5.79s  done               output={完整结构化结果}
```

注意 `llm.decide` 的 `tool_call` 在 **0.00s** 就到了，而它实际阻塞到 **3.88s** ——
这就是「等待期可见」的含义。

---

## 6. `token` / `done` / `error` 事件

### 6.1 `token`

```
event: token
data: {"token":"「编」的五笔"}
```

逐片给出最终答案正文，用于打字机效果。**它只用于展示**；
要拿结构化结果请用 `done.output`。

### 6.2 `done`（正常结束）

```
event: done
data: {"trace_id":"17a30aea…","latency_ms":5787,"output":{ …完整结构化结果… }}
```

| 字段 | 说明 |
|---|---|
| `trace_id` | 本次调用的链路 ID，排查问题时提供给平台方 |
| `latency_ms` | 平台侧总耗时 |
| `output` | **agent 的完整结构化结果**（等同于同步端点的 `output`）。可能不存在——那种情况下以 `token` 拼接内容为准 |

### 6.3 `error`（失败）

```
event: error
data: {"message":"…"}
```

收到即表示本轮失败，应停止渲染并提示用户。**流可能就此结束**，不要等待后续事件。

### 6.4 `conversation`（仅多轮会话）

```
event: conversation
data: {"conversation_id":"conv_…","turn":2,"history_truncated":false,"injected_turns":[1]}
```

仅当请求带 `metadata.conversation_id` 时出现，且**是流的第一条事件**。

---

## 7. HTTP 状态码与错误处理

| 状态 | 含义 | 上游应如何处理 |
|---|---|---|
| `200` | 成功建立流 | 开始逐事件读取 |
| `401` | 缺少 / 无效 / 已吊销的 API Key | **不要重试**，检查密钥配置 |
| `403` | 会话不属于当前 API Key | **不要重试**，检查会话来源 |
| `404` | agent 不存在 / 会话不存在 | **不要重试**，检查 agent 名称与会话 ID |
| `409` | **同一会话有请求正在进行**（会话内并发限制为 1） | 等上一轮结束再发；**不要立即重试** |
| `410` | **会话已过期**（默认 TTL 1 小时，可配至 24 小时） | 重新创建会话（§10）后重发 |
| `429` | 触发限流 / 会话轮次超上限（50 轮） | 退避重试；若为轮次上限则需新建会话 |
| `503` | agent 未运行且拉起失败 | 稍后重试 |
| `500/502` | 平台 / agent 内部错误 | 记录 `detail` 并告警 |

错误响应体为 `{"detail": "..."}`。

> ⚠️ **`409` 与 `410` 只会在多轮会话模式下出现**，且二者处置完全相反：
> `409` 该等（马上重试只会再次 409），`410` 该重建会话。混为一谈会让上游陷入死循环。

### 7.1 推荐的重试策略

- **只对流建立前的错误重试**：`429` / `503` / 网络层失败——指数退避，最多 2~3 次。
- **`409` 不要立刻重试**：会话内同一时刻只允许一个请求，立刻重试还是 409。
- **`410` 不重试原请求**：会话已过期，应按 §10 新建会话后重发。
- **`401` / `404` / `403` 一律不重试**：重试改变不了结果。
- **流已经开始后不要自动重试**：可能产生重复执行与重复计费；把选择权交给用户。

### 7.2 超时

| 项 | 建议值 | 说明 |
|---|---|---|
| 建立连接超时 | 10s | 超过则判定为平台不可用 |
| **读超时（空闲）** | **≥ 240s** | 单轮总耗时上限约 145s，故读取超时应留足余量 |
| 单次读取 | 不要设短超时 | 一次工具调用可能阻塞 60s+ 期间**没有任何事件** |

> ⚠️ 最常见的事故：把读超时设成 30~60s。模型推理单次可达 70s，
> 这期间流上不会有任何事件，短超时会把正常调用判死。

---

## 8. 多轮会话（可选）

首次请求带 `metadata.conversation_id` 即进入多轮模式（会话由平台维护）：

```json
{ "input": { "…": "…" }, "metadata": { "conversation_id": "conv_xxx" } }
```

- 每轮都会收到 `conversation` 事件，含 `turn`（轮次号）与 `injected_turns`
  （本轮实际注入给 agent 的历史轮次）；
- **每一轮都有自己完整的 `exec` 事件流**，按轮次分别展示即可；
- **会话内并发限制为 1**：上一轮没结束时再发会得到 `409`；
- **会话有 TTL**：默认 1 小时（可配，上限 24 小时），过期后请求返回 `410`，
  需重新创建会话；
- **单个会话最多 50 轮**，超出发 `429`，需新建会话；
- 历史过长时平台会自动裁剪，`history_truncated: true` 表示本轮历史被裁剪过。

---

## 9. 参数从哪来（agent 契约）

每个 agent 的入参形状不同，由平台方提供该 agent 的**契约**（`agent.yaml`）：

- 字段名、类型、必填与否、中文说明；
- 示例入参；
- 出参结构（用于解析 `done.output`）。

**不要在客户端硬编码某个 agent 的字段**；按契约文档构造即可。

---

## 10. 相关接口

| 用途 | 方法与路径 |
|---|---|
| 同步调用（无执行事件） | `POST /api/v1/agents/{name}/invoke` |
| 流式调用 | `POST /api/v1/agents/{name}/invoke/stream` |
| 异步调用 + webhook | `POST /api/v1/agents/{name}/invoke/async` |
| 创建会话 | `POST /api/v1/agents/{name}/conversations` |
| 查询会话 | `GET  /api/v1/agents/{name}/conversations/{cid}` |
| 删除会话 | `DELETE /api/v1/agents/{name}/conversations/{cid}` |

> 异步与同步端点**不返回执行事件**。需要过程可视化请用 `/invoke/stream`。

---

## 11. 最小可运行示例

### 11.1 Python（无第三方依赖）

```python
import json, urllib.request

def invoke_stream(base_url, agent, api_key, payload):
    req = urllib.request.Request(
        f"{base_url}/api/v1/agents/{agent}/invoke/stream",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        },
    )
    current, tools = None, {}
    with urllib.request.urlopen(req, timeout=240) as resp:
        for raw in resp:
            line = raw.decode().rstrip("\n")
            if line.startswith("event:"):
                current = line[6:].strip()
            elif line.startswith("data:") and current:
                data = line[5:].strip()
                if current == "exec":
                    ev = json.loads(data)
                    if ev["kind"] == "tool_call":
                        tools[ev["call_id"]] = ev
                        print(f"→ 开始调用 {ev['name']} 入参={ev.get('input')}")
                    elif ev["kind"] == "tool_result":
                        status = "成功" if ev.get("ok") else "失败"
                        print(f"← {ev['name']} {status} {ev.get('ms')}ms 出参={ev.get('output')}")
                    elif ev["kind"] == "log":
                        print(f"  [{ev.get('level')}] {ev.get('text')}")
                    elif ev["kind"] == "thinking":
                        print(f"  (思考) {ev.get('text')}")
                elif current == "token":
                    print(json.loads(data).get("token", ""), end="", flush=True)
                elif current == "done":
                    result = json.loads(data).get("output")
                    print(f"\n完成，结果字段：{list(result or {})}")
                elif current == "error":
                    raise RuntimeError(json.loads(data).get("message"))
                current = None
```

### 11.2 JavaScript / TypeScript（浏览器或 Node 18+）

```ts
type ExecEvent = {
  kind: string;
  seq?: number;
  text?: string;
  level?: string;
  call_id?: string;
  name?: string;
  input?: unknown;
  ok?: boolean;
  output?: unknown;
  error?: string;
  ms?: number;
  truncated?: boolean;
};

async function invokeStream(
  baseUrl: string,
  agent: string,
  apiKey: string,
  payload: unknown,
  onEvent: (ev: { event: string; data: string }) => void,
) {
  const resp = await fetch(`${baseUrl}/api/v1/agents/${agent}/invoke/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let event = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        onEvent({ event, data: line.slice(5).trim() });
        event = "";
      }
    }
  }
}
```

> 注意：**不要用 `EventSource`**。它只支持 GET，而本端点是 POST + 需要
> `Authorization` 头。请用 `fetch` + `ReadableStream` 自行解析（如上面示例）。

### 11.3 展示建议（与 coding agent 一致）

一行一次操作：

```
▼ 执行过程（4 次工具调用，1 条说明）
  ✓ tables.lookup   0ms     入参 {"char":"照"}   → {"hit":true,"code":"jvko"}
  ✓ tavily.search   4343ms  入参 {"query":"照 字的五笔编码是什么",…}  → {"texts":5}
  ✓ tavily.search   4523ms  入参 {"query":"五笔 照 拆字 字根 编码",…} → {"texts":5}
  ✓ llm.decide      3651ms  入参 {"model":"…","attempt":1}         → {"chars":552}
  · log  联网检索到的编码都没通过降噪校验，不采信
```

建议：
- 工具调用**按 `call_id` 折叠成一行**，不要 call/result 各占一行；
- `call` 到了就渲染「进行中」，result 到了再更新状态与耗时；
- `log` 按 `level` 着色；
- `thinking` 用引用样式并**明确标注**为模型内容；
- 面板**默认展开**，因为用户正是在等待时想看它。

---

## 12. 兼容性承诺

1. **事件是纯加法**：新增事件类型不影响老客户端（前提是你忽略未知事件）。
2. **`kind` 永不设白名单**：agent 可以发任意类型，平台只做透传。
3. **字段只增不改**：请容忍未知字段；对未知 `kind` 降级为通用条目展示。
4. **不处理 `exec` 也完全可用**：所有业务结果都在 `done.output` 里。
5. **同步 / 异步端点不受影响**：执行事件只出现在 `/invoke/stream`。
6. **体积上限**：单条事件的文本约 2KB、对象约 4KB，超限会被截断并标记
   `truncated: true`。看到该标记即表示内容不完整（但这只影响事件的展示，
   不影响 `done.output` 的完整性）。

---

## 13. 排查清单

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 完全没有 `exec` 事件 | 用了同步/异步端点；或该 agent 未接入执行事件 | 确认用 `/invoke/stream`；向平台方确认 agent 版本 |
| 事件只在最后一次性到达 | 中间层（nginx）缓冲了 SSE | 关闭 `proxy_buffering` |
| 流被中途掐断 | 读超时太短 | 读超时放到 ≥240s |
| `429` | 触发限流 | 退避重试 |
| `done.output` 为空但有 `token` | 该 agent 未提供结构化收尾块 | 用 `token` 拼接文本；向平台方反馈 |
| 事件里有 `truncated: true` | 单条事件超出体积上限 | 属正常保护，不影响最终结果 |
| 想知道某次调用卡在哪 | 拿 `trace_id` 找平台方查链路 | — |

---

## 14. 附：事件字段速查卡

```
exec
 ├─ kind      "tool_call" | "tool_result" | "log" | "thinking" | <自定义>
 ├─ seq       1,2,3…            每条流内单调递增
 ├─ call_id   "c1"              仅 tool_*，配对用
 ├─ name      "tavily.search"   仅 tool_*
 ├─ input     {…}               仅 tool_call
 ├─ ok        true|false|缺失   仅 tool_result
 ├─ output    {…}               仅 tool_result
 ├─ error     "…"               仅 tool_result（失败时）
 ├─ ms        4271              仅 tool_result
 ├─ level     info|warn|error   仅 log
 ├─ text      "…"               thinking / log
 └─ truncated true              任意（字段被截断时）

token   {"token": "正文分片"}
done    {"trace_id", "latency_ms", "output"}
error   {"message"}
conversation {"conversation_id","turn","history_truncated","injected_turns"}
```
