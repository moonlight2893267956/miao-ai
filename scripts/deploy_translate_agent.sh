#!/usr/bin/env bash
#
# deploy_translate_agent.sh — 将 translate-agent 正式注册进 miao-ai 平台并激活。
#
# 前置条件：
#   1. miao-ai 后端已运行（BASE_URL 可达）
#   2. 拥有管理员 token（MIAO_AI_ADMIN_TOKEN，带 login_required 的管理员 JWT）
#   3. 已配置腾讯云 COS（agent 代码包 zip 上传到 COS；storage.py 仅支持 COS 后端）
#
# 百度密钥：agent 自管百度，密钥不进代码仓库。
#   请在 miao-ai 运行环境（docker-compose / .env / 进程环境）注入以下变量，
#   agent 子进程会经 spawn_agent_process 继承父进程环境自动获得：
#     BAIDU_TRANSLATE_APPID / BAIDU_TRANSLATE_SECRET / BAIDU_TRANSLATE_ENDPOINT(可选)
#   未配置时 agent 自动降级为纯 LLM 翻译（output.notes 说明）。
#
# 用法：
#   MIAO_AI_ADMIN_TOKEN=<jwt> ./scripts/deploy_translate_agent.sh
#   可选覆盖：MIAO_AI_BASE_URL(默认 http://localhost:8000) VERSION(默认 1.0.0)
#
set -euo pipefail

BASE_URL="${MIAO_AI_BASE_URL:-http://localhost:8000}"
TOKEN="${MIAO_AI_ADMIN_TOKEN:-}"
VERSION="${VERSION:-1.0.0}"
AGENT_NAME="translate-agent"
SRC_DIR="$(cd "$(dirname "$0")/../demos/translate-agent" && pwd)"
TMP_ZIP="$(mktemp -d)/translate-agent.zip"

if [ -z "$TOKEN" ]; then
  echo "错误：请设置 MIAO_AI_ADMIN_TOKEN（管理员 JWT）" >&2
  exit 1
fi

AUTH="Authorization: Bearer ${TOKEN}"

echo "==> 打包 agent 源码： ${SRC_DIR}"
( cd "$SRC_DIR" && zip -r -q "$TMP_ZIP" . -x '*.pyc' '__pycache__/*' 'agent.log' )
echo "    包路径： ${TMP_ZIP}"

echo "==> 1/4 创建 agent（若已存在则跳过）"
HTTP=$(curl -s -o /tmp/agent_create.json -w "%{http_code}" -X POST "${BASE_URL}/api/v1/agents" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"name\":\"${AGENT_NAME}\",\"description\":\"翻译通用 Agent：百度打底 + LLM 润色/风格化/上下文连贯\"}")
if [ "$HTTP" = "201" ]; then
  echo "    已创建 agent=${AGENT_NAME}"
elif [ "$HTTP" = "409" ]; then
  echo "    agent 已存在，跳过创建"
else
  echo "    创建失败 HTTP=${HTTP}：" >&2; cat /tmp/agent_create.json >&2; exit 1
fi

echo "==> 2/4 上传版本 ${VERSION}（zip -> COS）"
HTTP=$(curl -s -o /tmp/ver_upload.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/versions" \
  -H "$AUTH" \
  -F "version=${VERSION}" \
  -F "entrypoint=agent:invoke" \
  -F "file=@${TMP_ZIP};filename=translate-agent.zip")
if [ "$HTTP" != "201" ]; then
  echo "    上传失败 HTTP=${HTTP}：" >&2; cat /tmp/ver_upload.json >&2; exit 1
fi
echo "    已上传版本 ${VERSION}"

echo "==> 3/4 激活版本 ${VERSION}（下载 zip + build venv + 启动子进程）"
HTTP=$(curl -s -o /tmp/ver_activate.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/versions/${VERSION}/activate" \
  -H "$AUTH")
if [ "$HTTP" != "200" ]; then
  echo "    激活失败 HTTP=${HTTP}：" >&2; cat /tmp/ver_activate.json >&2; exit 1
fi
echo "    已激活版本 ${VERSION}"

echo "==> 4/4 创建 API Key（toolbox 调用 agent 用）"
HTTP=$(curl -s -o /tmp/key_create.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/keys" \
  -H "$AUTH" -H "Content-Type: application/json" -d '{}')
if [ "$HTTP" != "201" ]; then
  echo "    建 key 失败 HTTP=${HTTP}：" >&2; cat /tmp/key_create.json >&2; exit 1
fi
API_KEY=$(python3 -c "import sys,json; print(json.load(sys.stdin)['key'])" < /tmp/key_create.json)

rm -rf "$(dirname "$TMP_ZIP")"

echo ""
echo "========== 部署完成 =========="
echo "把下面这行写进 miao-toolbox 的 .env（或 application-local.yml）："
echo ""
echo "MIAO_AI_AGENT_TRANSLATE_API_KEY=${API_KEY}"
echo ""
echo "并在 miao-ai 运行环境注入百度密钥（agent 子进程自动继承）："
echo "  BAIDU_TRANSLATE_APPID=xxx"
echo "  BAIDU_TRANSLATE_SECRET=xxx"
echo "  BAIDU_TRANSLATE_ENDPOINT=https://fanyi-api.baidu.com/api/trans/vip/translate   # 可选"
