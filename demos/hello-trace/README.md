# hello-trace

最小可用的 LangChain + Langfuse demo，验证 trace 链路通畅（用 DashScope 通义千问，OpenAI 兼容模式）。

执行后到 **Langfuse Cloud**（https://cloud.langfuse.com）查看 trace。

## 准备

1. 凭证已经写在 `.env`（或参考 `.env.example` 自行填入）
2. 安装依赖（包管理用 [uv](https://docs.astral.sh/uv/)）：
   ```bash
   uv venv
   uv pip install -r requirements.txt
   ```

## 跑

```bash
# 验证 Langfuse 连通性（不调 LLM）
uv run python smoke_test.py

# 跑完整 demo（调 DashScope + 上报 trace）
uv run python agent.py
```

## 预期

- smoke test：终端打印 `✅ Trace 已上报，trace_id = xxx`
- agent demo：终端打印 LLM 回答；Langfuse Cloud → 你的 project → Traces 出现一条新 trace
