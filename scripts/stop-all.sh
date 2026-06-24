#!/usr/bin/env bash
# 一键停止所有 miao-ai 服务
set -e

stopped=0
for f in /tmp/miao-backend.pid /tmp/miao-frontend.pid; do
  if [ -f "$f" ]; then
    pid=$(cat "$f")
    if ps -p "$pid" > /dev/null 2>&1; then
      echo "▶ 停止 PID $pid ($f)"
      kill "$pid" 2>/dev/null || true
      stopped=$((stopped + 1))
    fi
    rm -f "$f"
  fi
done

# 兜底：lsof 清端口
for port in 8000 3000; do
  pids=$(lsof -ti:$port 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "▶ 清端口 $port (PIDs: $pids)"
    echo "$pids" | xargs -r kill 2>/dev/null || true
    stopped=$((stopped + 1))
  fi
done

if [ "$stopped" -eq 0 ]; then
  echo "（没有运行中的服务）"
else
  echo "✅ 已停止 $stopped 个服务"
fi
