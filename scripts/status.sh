#!/usr/bin/env bash
# 看 miao-ai 各服务状态
set -e

echo "=== 后端 (8000) ==="
if [ -f /tmp/miao-backend.pid ] && ps -p "$(cat /tmp/miao-backend.pid)" > /dev/null 2>&1; then
  echo "  运行中 (PID $(cat /tmp/miao-backend.pid))"
  curl -s --max-time 5 http://localhost:8000/api/v1/health/ready -w "  /health/ready → [HTTP %{http_code}]\n" 2>&1 || true
else
  echo "  未运行"
fi

echo ""
echo "=== 前端 (3000) ==="
pid_alive=0
port_alive=0
if [ -f /tmp/miao-frontend.pid ] && ps -p "$(cat /tmp/miao-frontend.pid)" > /dev/null 2>&1; then
  pid_alive=1
  echo "  PID 文件存活: $(cat /tmp/miao-frontend.pid)"
else
  echo "  PID 文件不存在或进程已死"
fi
if lsof -nP -iTCP:3000 -sTCP:LISTEN > /dev/null 2>&1; then
  port_alive=1
  echo "  端口 3000 监听中"
else
  echo "  端口 3000 未监听"
fi
if [ $port_alive -eq 1 ]; then
  curl -s --max-time 5 -L http://localhost:3000/ -o /dev/null -w "  HTTP 健康检查 → [HTTP %{http_code}]\n" 2>&1 || true
  if [ $pid_alive -eq 0 ]; then
    echo "  ⚠️  端口在监但 PID 文件 stale — 实际是孤儿进程"
  fi
else
  echo "  ❌ 前端未运行"
fi

echo ""
echo "=== 数据库 (Neon) ==="
cd "$(dirname "$0")/.." && uv run --directory backend python -c "
import asyncio
from app.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
async def main():
    e = create_async_engine(settings.database_url)
    async with e.connect() as c:
        r = await c.execute(text('SELECT count(*) FROM agents'))
        print(f'  agents 表行数: {r.scalar()}')
    await e.dispose()
asyncio.run(main())
" 2>&1 | grep -E "(agents|Error)" || echo "  查不动（可能后端 venv 没装好）"
