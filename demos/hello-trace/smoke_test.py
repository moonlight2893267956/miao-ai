"""
Langfuse 连接 smoke test。

不调 LLM，只验证：
1. 凭证对不对
2. 能正常创建 trace/span 并 flush 上报

跑法：uv run python smoke_test.py
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

required = ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print(f"❌ 缺少环境变量：{missing}")
    sys.exit(1)

from langfuse import get_client

langfuse = get_client()

print("→ 检查 Langfuse 鉴权…")
if not langfuse.auth_check():
    print("❌ 鉴权失败，Public/Secret Key 有问题")
    sys.exit(1)
print("✅ 鉴权通过")

print("→ 上报测试 trace…")
with langfuse.start_as_current_observation(
    as_type="span",
    name="miao-smoke-test",
    input={"check": "hello from miao-ai"},
) as span:
    span.update(output={"ok": True, "ts": "phase-0 verification"})

langfuse.flush()
print(f"✅ Trace 已上报，trace_id = {span.trace_id}")
print(f"👉 去 {os.getenv('LANGFUSE_BASE_URL')} 看看")
