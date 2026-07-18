# Cron 表达式编辑器 AI 智能辅助 Agent（cron-assistant）

为「阿渺工具箱 · Cron 表达式编辑器」提供五种 AI 增强能力，与 regex-assistant / diff-explainer / json-repairer 同体系，运行在 miao-ai 平台（单文件 agent，Docker 构建仅 COPY agent.py）。

## 五大任务

| task | 说明 | 必需输入 | 输出关键字段 |
|------|------|----------|--------------|
| generate | 自然语言转 Cron 表达式 | description（可选 dialect） | expression, dialect, explanation |
| explain | Cron 表达式转自然语言详解 | expression（可选 dialect） | explanation |
| optimize | 表达式优化/简化建议 | expression（可选 dialect） | suggestions[] |
| diagnose | 排错诊断（支持多轮） | expression + phenomenon | diagnosis, **expression**（修正后的标准表达式，非空时前端提供「采纳修正」） |
| convert | linux5 与 spring6 方言转换 | expression + targetDialect | originalExpression, convertedExpression, dialect, targetDialect, notes |

方言：linux5（分 时 日 月 星期）/ spring6（秒 分 时 日 月 星期）。generate 未指定方言时默认 linux5；convert 未给源方言时按字段数推断。

## 输入协议（调用 invoke(input, config)）

```
{
  "task": "generate",
  "description": "每周一至周五上午9点30分执行",
  "dialect": "linux5"
}
```

可选增强字段（由前端/后端本地能力注入，降低幻觉）：

- explanationContext：本地 humanizer 输出
- semanticWarnings：本地 validator 警告列表
- timezone：关联时区
- conversation：diagnose 多轮历史，格式为 [{ "role": "user" 或 "assistant", "content": "..." }]

## 输出协议

返回结构化 JSON，output.answer 为顶层便捷字段（前端 Try Run 普通模式直接展示）：

- generate 的 answer = expression
- explain 的 answer = explanation
- optimize 的 answer = suggestions 拼接
- diagnose 的 answer = diagnosis
- convert 的 answer = convertedExpression

流式（config["stream"] = true）时逐 token 输出 {"token": ...}。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| LLM_API_KEY / DASHSCOPE_API_KEY | 无 | LLM API Key |
| LLM_BASE_URL / DASHSCOPE_BASE_URL | 无 | OpenAI 兼容 base url |
| LLM_MODEL / DASHSCOPE_MODEL | qwen-plus | 模型名 |
| LLM_TEMPERATURE | 0.3 | 温度 |
| LLM_MAX_TOKENS | 4096 | 最大 token |
| LLM_TIMEOUT | 120 | 单次 LLM 超时（秒） |
| AGENT_TOTAL_TIMEOUT | 240 | Agent 总超时守护（秒） |

## 工程特性（复用 regex-assistant 模式）

- _build_llm()：max_retries=0 + timeout，禁用 SDK 内部重试
- TOTAL_TIMEOUT + _check_timeout(stage)：Agent 级总超时守护
- MAX_LLM_CALLS=2：LLM 调用失败自动重试
- _strip_markdown_fence()：兼容 LLM 输出被 markdown 包裹
- 单文件打包：业务代码全内联，仅第三方依赖经 requirements.txt

## 安全网

Agent 只产出表达式文本；表达式合法性、方言回填、5 转 6 字段丢失确认等由前端本地 parseExpression / validate 在调用后校验（见设计文档 5.2），AI 产出绝不污染编辑器状态。
