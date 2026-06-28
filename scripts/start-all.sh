#!/usr/bin/env bash
# 一键启动 miao-ai 所有服务（后端 + 前端）
# 跑法：bash scripts/start-all.sh
# 停服：bash scripts/stop-all.sh
# 看状态：bash scripts/status.sh
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ===== 工具检查 =====
need() {
  if ! command -v "$1" > /dev/null 2>&1; then
    echo "❌ 找不到 $1，请先安装："
    case "$1" in
      uv)   echo "   brew install uv  或  curl -LsSf https://astral.sh/uv/install.sh | sh" ;;
      pnpm) echo "   brew install pnpm  或  npm i -g pnpm" ;;
      node) echo "   装 nvm 后 nvm install 22（Next.js 14 要 Node ≥ 18.17）" ;;
    esac
    exit 1
  fi
}
need uv
need pnpm
need node

NODE_MAJOR=$(node -e "process.stdout.write(process.versions.node.split('.')[0])")
if [ "$NODE_MAJOR" -lt 20 ]; then
  echo "⚠️  Node $(node --version) 比较老（Next.js 14 要 ≥ 18.17，建议 20+）"
  echo "   换高版本：nvm install 22 && nvm use 22"
fi

# 找 node 22（如果有 nvm）
if [ -d "$HOME/.nvm/versions/node" ]; then
  LATEST_NODE=$(ls -1 "$HOME/.nvm/versions/node" | grep -E "^v(2[0-9]|18\.(1[7-9]|[2-9][0-9]))" | sort -V | tail -1)
  if [ -n "$LATEST_NODE" ]; then
    export PATH="$HOME/.nvm/versions/node/$LATEST_NODE/bin:$PATH"
    echo "▶ 用 Node $(node --version)"
  fi
fi

# ===== 凭证加载（本地凭证隔离生产）=====
# 优先 .env.local（gitignore，本地开发用）→ 隔离生产 Langfuse/MySQL
# 回退根 .env（生产凭证）→ 会污染生产数据，仅应急用
if [ -f .env.local ]; then
  echo "▶ 加载本地凭证 .env.local（隔离生产 Langfuse/MySQL）"
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
elif [ -f .env ]; then
  echo "⚠️  未找到 .env.local，回退到根 .env（会写到生产 Langfuse/MySQL）"
  echo "   建议：cp .env.local.example .env.local 并填本地凭证"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "❌ 找不到任何 .env 文件"
  echo "   选项 1（推荐）：cp .env.local.example .env.local 并填本地凭证"
  echo "   选项 2（生产应急）：cp .env.example .env 并填凭证"
  exit 1
fi

# ===== 后端 =====
if [ ! -f backend/.venv/bin/python ]; then
  echo "▶ 创建后端 venv..."
  (cd backend && uv venv)
fi
echo "▶ 装后端依赖（如有更新）..."
(cd backend && uv pip install -e ".[dev]" 2>&1 | tail -3)

# ===== 前端 =====
if [ ! -d frontend/node_modules ]; then
  echo "▶ 装前端依赖（首次需要几分钟）..."
  (cd frontend && pnpm install 2>&1 | tail -3)
fi

# ===== 检查端口 =====
for port in 8000 3000; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN > /dev/null 2>&1; then
    echo "⚠️  端口 $port 已被占用："
    lsof -nP -iTCP:$port -sTCP:LISTEN | head -3
    echo "   如需重启，先跑 bash scripts/stop-all.sh"
    exit 1
  fi
done

# ===== 启动后端（后台） =====
echo "▶ 启动后端 (8000)..."
cd "$ROOT/backend"
nohup uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > /tmp/miao-backend.log 2>&1 &
echo $! > /tmp/miao-backend.pid

# ===== 启动前端（后台） =====
echo "▶ 启动前端 (3000)..."
cd "$ROOT/frontend"
nohup pnpm dev > /tmp/miao-frontend.log 2>&1 &
echo $! > /tmp/miao-frontend.pid

# ===== 等服务就绪 =====
cd "$ROOT"
echo "▶ 等服务就绪..."
for i in 1 2 3 4 5 6 7 8 9 10; do
  BACKEND_OK=$(curl -s --max-time 1 http://localhost:8000/api/v1/health > /dev/null 2>&1 && echo y || echo n)
  FRONTEND_OK=$(curl -s --max-time 1 -L http://localhost:3000/ > /dev/null 2>&1 && echo y || echo n)
  if [ "$BACKEND_OK" = "y" ] && [ "$FRONTEND_OK" = "y" ]; then
    break
  fi
  sleep 2
done

echo ""
echo "✅ 启动完成"
echo "   后端  http://localhost:8000    (PID $(cat /tmp/miao-backend.pid),  日志 /tmp/miao-backend.log)"
echo "   前端  http://localhost:3000    (PID $(cat /tmp/miao-frontend.pid),  日志 /tmp/miao-frontend.log)"
echo "   OpenAPI  http://localhost:8000/docs"
echo ""
echo "   停服：bash scripts/stop-all.sh"
echo "   状态：bash scripts/status.sh"
