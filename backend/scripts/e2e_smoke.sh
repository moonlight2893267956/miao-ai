#!/usr/bin/env bash
# 端到端冒烟测试：覆盖完整链路（create agent → upload → activate → invoke）
# 跑法：先启动 server (uv run uvicorn app.main:app --port 8000)，
#       然后 bash scripts/e2e_smoke.sh

set -e
BASE="${BASE:-http://localhost:8000}"
NAME="smoke-$(date +%s)-$RANDOM"
ZIP="/tmp/miao-smoke-agent.zip"

echo "🔍 1) Server health"
curl -fsS "$BASE/api/v1/health/ready" > /dev/null && echo "   ✅ ready"

echo "🔍 2) Create agent: $NAME"
curl -fsS -X POST "$BASE/api/v1/agents" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$NAME\",\"description\":\"smoke test\"}" | head -c 200
echo ""

echo "🔍 3) Build sample agent zip"
SAMPLE_DIR="$(dirname "$0")/../../demos/sample-agent"
if [ ! -f "$SAMPLE_DIR/agent.py" ]; then
  echo "   ❌ sample agent not found at $SAMPLE_DIR"
  exit 1
fi
(cd "$SAMPLE_DIR" && zip -j "$ZIP" agent.py requirements.txt) > /dev/null
echo "   ✅ $ZIP ($(stat -f%z "$ZIP") bytes)"

echo "🔍 4) Upload v1"
curl -fsS -X POST "$BASE/api/v1/agents/$NAME/versions" \
  -F "version=v1" \
  -F "file=@$ZIP" | head -c 200
echo ""

echo "🔍 5) Activate v1（首次会构建 venv，1~2 分钟）"
curl -fsS --max-time 240 -X POST "$BASE/api/v1/agents/$NAME/versions/activate?version=v1" \
  -w "\n   [HTTP %{http_code}]"

echo "🔍 6) Issue API key"
KEY=$(curl -fsS -X POST "$BASE/api/v1/agents/$NAME/keys" \
  -H "Content-Type: application/json" \
  -d '{"label":"smoke"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['key'])")
echo "   ✅ key: ${KEY:0:15}..."

echo "🔍 7) Invoke"
RESP=$(curl -fsS --max-time 30 -X POST "$BASE/api/v1/agents/$NAME/invoke" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"input":{"question":"一句话介绍你自己"},"metadata":{"user_id":"smoke","session_id":"s1","tags":["smoke"]}}')
echo "$RESP" | head -c 400
echo ""
TRACE_ID=$(echo "$RESP" | python3 -c "import sys, json; print(json.load(sys.stdin).get('trace_id', ''))")
echo "   ✅ trace_id: $TRACE_ID"

echo ""
echo "🎉 端到端通过！"
echo "   agent:    $NAME"
echo "   trace_id: $TRACE_ID"
echo "   👉 去 https://cloud.langfuse.com 看 trace（tag: smoke, agent:$NAME）"
