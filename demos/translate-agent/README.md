# translate-agent（翻译通用 Agent）

一个「AI 增强层」翻译 agent：站在百度机器翻译之上，用 LLM 补足机械翻译做不到的
**通顺润色 / 风格化 / 上下文连贯**。采用「一个 agent + 可配置 `task`」模式，而非为每种能力各做一个 agent。

> MVP 阶段：落在 `demos/` 下验证。现已由 miao-toolbox story-4.1 正式化——
> 通过 `miao-ai/scripts/deploy_translate_agent.sh` 注册为 backend 正式 agent 并激活（详见该脚本）。
> 百度调用权归 agent 自管（密钥注入 miao-ai 运行环境），toolbox 仅传 text/tone。

## 能力（按 `input.task` 分发）

| task | 说明 | 依赖百度 |
| --- | --- | --- |
| `translate`（默认） | 百度机器翻译打底 + LLM 润色融合；也可纯 LLM 翻译 | 是（可降级） |
| `polish` | 在已有文本上做风格化润色，不改语义只改表达 | 否 |
| `context` | 带前文上下文 + 术语库的连贯翻译（解决指代/术语一致） | 否 |

## 文件结构

```
demos/translate-agent/
├── agent.py          # 入口 invoke(input, config)，按 task 分发；超时/重试/流式
├── baidu_client.py   # 百度翻译客户端 + MD5 签名，自管 appid/secret
├── prompts.py        # 三任务 system prompt + tone/domain/glossary 动态拼装
├── requirements.txt
└── README.md
```

## input / output schema

### input

```jsonc
{
  "task": "translate",          // translate | polish | context，默认 translate
  "text": "待处理文本",           // 必填
  "original": "原文",            // polish 可选，用于保持语义
  "source_lang": "auto",         // auto | zh | en | jp ...
  "target_lang": "en",
  "tone": "formal",              // formal | casual | business | literary | academic
  "domain": "tech",              // tech | medical | legal | finance
  "glossary": [{"src": "内核", "tgt": "kernel"}],
  "context": "前文/对话历史",      // context 任务用
  "mt_provider": "baidu"         // translate 任务：baidu(默认) | llm
}
```

### output

```jsonc
{
  "task": "translate",
  "translated": "...",                       // 最终译文/润色结果（所有任务都有）
  "mt_draft": "百度原始译文",                  // translate + baidu 模式返回
  "bilingual": [{"src": "...", "tgt": "..."}], // 双语对照，便于前端双栏
  "source_lang": "zh",
  "target_lang": "en",
  "notes": "润色/降级说明",
  "glossary_applied": [...]                   // context 任务应用的术语
}
```

## 环境变量

### LLM（从 miao_runner 父进程继承，与其他 demo 一致）

| 变量 | 说明 | 兜底 |
| --- | --- | --- |
| `LLM_MODEL` | 模型名 | `DASHSCOPE_MODEL` → `qwen-plus` |
| `LLM_API_KEY` | API Key | `DASHSCOPE_API_KEY` |
| `LLM_BASE_URL` | Base URL | `DASHSCOPE_BASE_URL` |
| `LLM_TEMPERATURE` | 温度 | `0.3` |
| `LLM_MAX_TOKENS` | 最大 token | `4096` |
| `LLM_TIMEOUT` | 单次 LLM 超时(s) | `120` |
| `AGENT_TOTAL_TIMEOUT` | agent 总超时(s) | `240` |

### 百度翻译（agent 自管）

| 变量 | 说明 |
| --- | --- |
| `BAIDU_TRANSLATE_APPID` | 百度翻译开放平台 appid |
| `BAIDU_TRANSLATE_SECRET` | 密钥 |
| `BAIDU_TRANSLATE_ENDPOINT` | 可选，默认通用文本翻译 endpoint |
| `BAIDU_TRANSLATE_TIMEOUT` | 可选，默认 `10` 秒 |

> 未配置百度密钥时，`translate` 任务会自动降级为纯 LLM 翻译，并在 `notes` 中说明，不会崩溃。

## 本地运行

```bash
cd demos/translate-agent
pip install -r requirements.txt

export LLM_API_KEY=sk-xxx
export LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export BAIDU_TRANSLATE_APPID=your_appid       # 可选
export BAIDU_TRANSLATE_SECRET=your_secret     # 可选

python -c "from agent import invoke; import json; \
print(json.dumps(invoke({'task':'translate','text':'今天天气不错','target_lang':'en'}), ensure_ascii=False, indent=2))"
```

## 签名算法（百度 MD5）

```
sign = md5(appid + q + salt + secret)
```

其中 `q` 为待翻译全文、`salt` 为随机串；请求参数含 `q, from, to, appid, salt, sign`，
`from=auto` 可自动语种识别。

## 工程化要点（复用 json-repairer 模式）

- `_build_llm()`：`max_retries=0` + `timeout`，禁用 SDK 内部重试，agent 自己重试
- `TOTAL_TIMEOUT` + `_check_timeout(stage)`：agent 总超时守护（须在平台 `invoke_sync_timeout` 之内）
- `MAX_LLM_CALLS`：LLM 调用失败重试
- `_normalize_llm_output()`：剥离 markdown fence
- 流式：`config["stream"]=True` 时，对 LLM 部分逐 token yield `{"token": ...}`
- Langfuse trace 由 `miao_runner` 自动处理，agent 无需改动

## 计费与审计

百度按**字符数**计费（不同于 LLM 的 token）。`baidu_client.py` 已对每次调用记录字符数日志，
并预留 `usage_recorder(char_count, source_lang, target_lang)` 审计接口。MVP 阶段仅打日志，
不驱动前端；正式化时可接入 `BaiduInvocationRecorder`。

## 正式化路径（超出本 MVP）

1. 注册进 `backend/app/runtime/registry`，成为平台一等 agent
2. 接入字符级审计 `BaiduInvocationRecorder`，驱动前端用量展示
3. 长文分段翻译 + 跨段术语一致、图片/语音翻译、术语库持久化、批量导出等
