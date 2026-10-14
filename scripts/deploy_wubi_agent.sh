#!/usr/bin/env bash
#
# deploy_wubi_agent.sh — 把 wubi-explainer 注册进 miao-ai 平台并激活。
#
# 前置条件：
#   1. miao-ai 后端已运行（BASE_URL 可达）
#   2. 拥有管理员 token（MIAO_AI_ADMIN_TOKEN，带 login_required 的管理员 JWT）
#   3. 已配置腾讯云 COS（agent zip 上传到 COS）
#
# 用法：
#   MIAO_AI_ADMIN_TOKEN=<jwt> ./scripts/deploy_wubi_agent.sh
#   可选覆盖：MIAO_AI_BASE_URL（默认 http://localhost:8000） VERSION（默认 1.0.0）
#
# 注意：同版本号二次上传会被拒（409），改 VERSION 即可；改完代码必须重新激活才生效。
#
set -euo pipefail

BASE_URL="${MIAO_AI_BASE_URL:-http://localhost:8000}"
TOKEN="${MIAO_AI_ADMIN_TOKEN:-}"
VERSION="${VERSION:-1.0.0}"
AGENT_NAME="wubi-explainer"
SRC_DIR="$(cd "$(dirname "$0")/../demos/wubi-explainer" && pwd)"
TMP_ZIP="$(mktemp -d)/wubi-explainer.zip"

if [ -z "$TOKEN" ]; then
  echo "错误：请设置 MIAO_AI_ADMIN_TOKEN（管理员 JWT）" >&2
  exit 1
fi

AUTH="Authorization: Bearer ${TOKEN}"

echo "==> 打包 agent 源码： ${SRC_DIR}"
( cd "$SRC_DIR" && zip -r -q "$TMP_ZIP" . -x '*.pyc' '__pycache__/*' 'agent.log' )
unzip -l "$TMP_ZIP" | sed 's/^/      /'

# 上传前的预检：用**平台的判据**，别等传上去才被 400 拒。
#
# 平台是精确匹配（versions.py）：
#     names = zf.namelist()
#     if "agent.py" not in names:  → 400 "zip must contain 'agent.py' (entrypoint)"
# 所以 './agent.py' 和 'wubi-explainer/agent.py' 都会被拒 —— 后者是手工打包最常见的结果
# （在父目录 zip，或用 Finder「压缩」，还会多带 __MACOSX/）。
# 这里连入口函数一起预检：平台是在子进程 exec_module 时才发现的，那时错误只剩
# 一个 500/crashed，什么信息都没有。
ENTRYPOINT="${ENTRYPOINT:-agent:invoke}"
echo "==> 0/4 预检（包结构 + 入口 + 非法输入不抛异常）"
python3 - "$TMP_ZIP" "$SRC_DIR" "${ENTRYPOINT#*:}" <<'PY' || exit 1
import importlib.util, os, sys, zipfile

zip_path, src_dir, func_name = sys.argv[1], sys.argv[2], sys.argv[3]

with zipfile.ZipFile(zip_path) as zf:
    names = zf.namelist()

if "agent.py" not in names:
    print("    ✗ 包内没有根级 agent.py（平台会回 400）。实际条目：")
    for n in names:
        print("        ", repr(n))
    print("    常见原因：在父目录打包 → 'wubi-explainer/agent.py'；")
    print("             用 Finder「压缩」→ 多一层目录 + __MACOSX/")
    sys.exit(1)

spec = importlib.util.spec_from_file_location(
    "preflight_agent", os.path.join(src_dir, "agent.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if not hasattr(mod, func_name):
    print(f"    ✗ 找不到入口函数 {func_name}（entrypoint 声明为 {func_name}）")
    sys.exit(1)

# 入口的「绝不抛异常」保证：非法输入必须如实返回 dict
out = mod.invoke({"char": "照", "code": "jvko", "positions": []}, {})
if not isinstance(out, dict):
    print(f"    ✗ invoke 对非法输入返回了 {type(out).__name__}，应为 dict")
    sys.exit(1)

print(f"    ✓ 包含根级 agent.py、入口 {func_name} 存在、非法输入如实返回 dict")
PY

echo "==> 1/4 创建 agent（已存在则跳过）"
HTTP=$(curl -s -o /tmp/wubi_agent_create.json -w "%{http_code}" -X POST "${BASE_URL}/api/v1/agents" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"name\":\"${AGENT_NAME}\",\"description\":\"五笔拆字解释：在给定候选字根内挑选并讲清理由\"}")
if [ "$HTTP" = "201" ]; then
  echo "    已创建 agent=${AGENT_NAME}"
elif [ "$HTTP" = "409" ]; then
  echo "    agent 已存在，跳过创建"
else
  echo "    创建失败 HTTP=${HTTP}：" >&2; cat /tmp/wubi_agent_create.json >&2; exit 1
fi

echo "==> 2/4 上传版本 ${VERSION}（zip -> COS）"
HTTP=$(curl -s -o /tmp/wubi_ver_upload.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/versions" \
  -H "$AUTH" \
  -F "version=${VERSION}" \
  -F "entrypoint=agent:invoke" \
  -F "file=@${TMP_ZIP};filename=wubi-explainer.zip")
if [ "$HTTP" != "201" ] && [ "$HTTP" != "200" ]; then
  echo "    上传失败 HTTP=${HTTP}（同版本重复上传会 409，改 VERSION 即可）：" >&2
  cat /tmp/wubi_ver_upload.json >&2; exit 1
fi
echo "    已上传"

echo "==> 3/4 激活版本 ${VERSION}（下载 zip + build venv + 启动子进程）"
HTTP=$(curl -s -o /tmp/wubi_ver_activate.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/versions/${VERSION}/activate" -H "$AUTH")
if [ "$HTTP" != "200" ]; then
  echo "    激活失败 HTTP=${HTTP}（响应 detail 里通常是 venv build 的报错）：" >&2
  cat /tmp/wubi_ver_activate.json >&2; exit 1
fi
echo "    已激活"

echo "==> 4/4 创建 API key"
HTTP=$(curl -s -o /tmp/wubi_key_create.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/keys" \
  -H "$AUTH" -H "Content-Type: application/json" -d '{}')
if [ "$HTTP" != "201" ] && [ "$HTTP" != "200" ]; then
  echo "    创建 key 失败 HTTP=${HTTP}：" >&2; cat /tmp/wubi_key_create.json >&2; exit 1
fi

API_KEY=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('key',''))" < /tmp/wubi_key_create.json)
if [ -z "$API_KEY" ]; then
  echo "    未能从响应里取到 key：" >&2; cat /tmp/wubi_key_create.json >&2; exit 1
fi

SAMPLE_INPUT='{"input":{"task":"explain-split","char":"照","code":"jvko","positions":[{"index":0,"key":"j","candidates":[{"glyph":"日","name":"日字"},{"glyph":"曰","name":"曰字"}]},{"index":1,"key":"v","candidates":[{"glyph":"刀","name":"刀字"}]},{"index":2,"key":"k","candidates":[{"glyph":"口","name":"口字"}]},{"index":3,"key":"o","candidates":[{"glyph":"灬","name":"四点底"}]}],"notes":[]},"metadata":{}}'

# 预热：agent 刚激活时模型侧是冷的，第一次真实调用极可能撞上调用方的读超时
# （实测：冷启动单次 >70s，而 MiaoAiClient 默认读超时 60s × 3 次重试 = 3 分钟「分析中」）。
# 这里替用户先挨这一下，预热失败不影响部署结果，只做提示。
echo "==> 5/5 预热（消除冷启动，避免第一次真实调用超时）"
WARM_HTTP=$(curl -s --max-time 240 -o /tmp/wubi_warm.json -w "%{http_code}" -X POST \
  "${BASE_URL}/api/v1/agents/${AGENT_NAME}/invoke" \
  -H "Authorization: Bearer ${API_KEY}" -H "Content-Type: application/json" \
  -d "$SAMPLE_INPUT")
echo "    预热调用 HTTP=${WARM_HTTP}"
python3 - <<'PY'
import json
try:
    with open('/tmp/wubi_warm.json') as f:
        out = json.load(f).get('output', {})
    sc = out.get('self_check') or {}
    if out.get('error'):
        print('    ⚠ agent 报错:', out['error'])
    else:
        print(f"    self_check.ok = {sc.get('ok')}（重试 {sc.get('attempts')} 次）")
        if sc.get('ok'):
            print('    预热成功 —— 用户第一次点击就是热的')
        else:
            print('    自校验未通过，violations:', sc.get('violations'))
except Exception as exc:
    print('    预热响应解析失败:', exc)
PY

cat <<EOF

✅ 部署完成

  agent 名   ${AGENT_NAME}
  版本       ${VERSION}
  API key    ${API_KEY}
             !! 明文只显示这一次，请立刻保存 !!

接下来三步（少了任何一步前端都会拿到可解释的失败）：
  1) 在平台里给该 agent 绑定模型 —— 否则 LLM_API_KEY 不会被注入，
     agent 会直接返回「模型未配置」。
  2) 把 key 写进 miao-toolbox 的 Nacos miao-ai.yaml（dev/prod 各自的 SECRET_GROUP）：
       miao.ai.agents.wubi-explainer.api-key: ${API_KEY}
     并在 .env.example 补 MIAO_AI_AGENT_WUBI_* 四个占位变量。切不要提交进 git。
  3) 自测（可直接照抄）：
       curl -s -X POST ${BASE_URL}/api/v1/agents/${AGENT_NAME}/invoke \\
         -H "Authorization: Bearer ${API_KEY}" -H 'Content-Type: application/json' \\
         -d '${SAMPLE_INPUT}'
     期望看到 output.positions 是 4 项、self_check.ok 为 true。
     若 self_check.ok=false，看 self_check.violations —— 那是模型没能在候选里挑对。
EOF
