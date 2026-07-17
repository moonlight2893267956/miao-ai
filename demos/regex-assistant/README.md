# regex-assistant

正则表达式 AI 助手，为阿渺工具箱的正则测试器提供三种 AI 增强能力。

## 能力

| 任务 | 说明 | 必填输入 |
|---|---|---|
| `generate` | 根据自然语言描述生成正则表达式 | `description` |
| `explain` | 逐段解释给定正则表达式的含义 | `pattern` |
| `optimize` | 对给定正则表达式提出优化建议 | `pattern` |

## 输入 Schema

```json
{
  "task": "generate | explain | optimize",
  "description": "自然语言描述（generate 时必填）",
  "pattern": "正则表达式（explain/optimize 时必填）",
  "flags": "当前标志位（可选）",
  "engine": "当前引擎（可选，js/java/python/go/php）"
}
```

## 输出 Schema

```json
{
  "task": "generate",
  "pattern": "生成的/优化后的正则表达式",
  "explanation": "解释文本",
  "suggestions": ["建议1", "建议2"],
  "answer": "pattern 的副本，便于前端 Try Run 直接展示"
}
```

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `LLM_API_KEY` / `DASHSCOPE_API_KEY` | ✅ | LLM API 密钥 |
| `LLM_BASE_URL` / `DASHSCOPE_BASE_URL` | ❌ | LLM API 地址（默认 DashScope） |
| `LLM_MODEL` / `DASHSCOPE_MODEL` | ❌ | 模型名（默认 qwen-plus） |
| `LLM_TEMPERATURE` | ❌ | 温度（默认 0.3） |
| `LLM_TIMEOUT` | ❌ | 单次 LLM 超时秒数（默认 120） |
| `AGENT_TOTAL_TIMEOUT` | ❌ | Agent 总超时秒数（默认 240） |

## 本地运行

```bash
cd demos/regex-assistant
set -a && . ../../.env && set +a

# 同步调用
python3 -c "
from agent import invoke
import json
out = invoke({'task':'generate','description':'匹配中国大陆手机号','engine':'js'})
print(json.dumps(out, ensure_ascii=False, indent=2))
"

# 流式调用
python3 -c "
from agent import invoke
for chunk in invoke({'task':'explain','pattern':'(?<=@)\\\\w+\\\\.\\\\w+','engine':'js'}, config={'stream':True}):
    print(chunk, end=' ')
print()
"
```

## 平台部署

1. 打包 zip：`zip -r regex-assistant.zip agent.py requirements.txt README.md`
2. 在 miao-ai 平台创建 agent `regex-assistant`，上传 zip，建版本并 activate
3. miao-toolbox-api 通过 `MiaoAiClient.invoke("regex-assistant", input, metadata)` 调用

## 引擎兼容性说明

Agent 会根据 `engine` 参数调整输出：

| 引擎 | 限制 |
|---|---|
| `go` | RE2 引擎，不支持前瞻/后顾断言、反向引用 |
| `java` | Java 8 不支持 `s` (dotAll) 标志，Java 17+ 支持 |
| `python` | `\d` 匹配 Unicode 数字，与其他引擎不同 |
| `js` | `\d` 仅匹配 `[0-9]`，ES2018+ 才支持后行断言 |
| `php` | PCRE 引擎，功能较完整 |
