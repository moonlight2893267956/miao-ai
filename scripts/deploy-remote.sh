#!/usr/bin/env bash
# ===================================================================
# miao-ai — 远程纯部署脚本（由 CI 通过 SSH 调用）
# ===================================================================
# 在服务器上执行，只做：登录 GHCR → 拉镜像 → 重启 → 迁移 → 健康检查
#
# 前提：$DEPLOY_DIR 已完成首次 bootstrap（有 .env、miao-infra 已启动）
# 用法（由 CI cd.yml/rollback.yml 自动调用，无需手动执行）:
#   DEPLOY_DIR=/opt/miao-ai IMAGE_TAG=prod \
#   GHCR_TOKEN=xxx GHCR_OWNER=xxx /tmp/deploy-remote.sh
# ===================================================================

set -euo pipefail

# ===== 配置（由 CI 注入） =====
DEPLOY_DIR="${DEPLOY_DIR:-/opt/miao-ai}"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.prod.yml"
ENV_FILE="$DEPLOY_DIR/.env"
COMPOSE_CMD="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

GHCR_TOKEN="${GHCR_TOKEN:?必须设置 GHCR_TOKEN}"
GHCR_OWNER="${GHCR_OWNER:?必须设置 GHCR_OWNER}"

# 普通部署=prod；回滚时由 CI 传入旧 sha 标签（如 sha-a1b2c3d）
IMAGE_TAG="${IMAGE_TAG:-prod}"
export IMAGE_TAG

# 镜像仓库前缀（与 GHCR 仓库名一致，owner 强制小写）
REPO_LOWER=$(echo "$GHCR_OWNER" | tr '[:upper:]' '[:lower:]')
BACKEND_IMAGE="ghcr.io/${REPO_LOWER}/miao-ai/backend"
FRONTEND_IMAGE="ghcr.io/${REPO_LOWER}/miao-ai/frontend"

# ===== 工具函数 =====
red()  { printf "\033[31m%s\033[0m\n" "$*"; }
grn()  { printf "\033[32m%s\033[0m\n" "$*"; }
ylw()  { printf "\033[33m%s\033[0m\n" "$*"; }
hdr()  { printf "\n\033[1;36m=== %s ===\033[0m\n" "$*"; }

# ===== 步骤 =====

step_check_prereqs() {
  hdr "0. 检查前置条件"
  if [ ! -f "$ENV_FILE" ]; then
    red "  ✗ 未找到 $ENV_FILE（请先在服务器上完成首次 bootstrap: 放置 .env）"
    exit 1
  fi
  if [ ! -f "$COMPOSE_FILE" ]; then
    red "  ✗ 未找到 $COMPOSE_FILE"
    exit 1
  fi
  grn "  ✓ 前置条件满足"
}

step_ensure_net() {
  hdr "1. 确保 miao-infra-net 网络存在"
  if docker network inspect miao-infra-net >/dev/null 2>&1; then
    grn "  ✓ miao-infra-net 已存在"
  else
    ylw "  ⚠ miao-infra-net 不存在, 创建空网络(若 miao-infra 未启动, backend 将连不上 MySQL/Redis)"
    docker network create miao-infra-net >/dev/null 2>&1 || true
    grn "  ✓ 已创建 miao-infra-net"
  fi
}

step_login_ghcr() {
  hdr "2. 登录 GHCR（拉取私有镜像）"
  echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_OWNER" --password-stdin
  grn "  ✓ GHCR 登录成功(user=$GHCR_OWNER)"
}

step_verify_image() {
  # prod 一定存在, 跳过校验；回滚标签需确认真实存在, 避免 pull 时 manifest unknown
  if [ "$IMAGE_TAG" = "prod" ]; then
    return 0
  fi
  hdr "2.5 验证回滚镜像标签 $IMAGE_TAG"
  if ! docker manifest inspect "${BACKEND_IMAGE}:${IMAGE_TAG}" >/dev/null 2>&1; then
    red "  ✗ 镜像 ${BACKEND_IMAGE}:${IMAGE_TAG} 不存在!"
    red "  该提交可能从未成功推送过镜像到 GHCR。"
    echo "  排查: 在 GitHub Packages 页面查看可用标签, 或重新 push main 触发普通部署。"
    exit 1
  fi
  grn "  ✓ 镜像标签 $IMAGE_TAG 存在"
}

step_pull_and_up() {
  hdr "3. 拉取镜像并重启服务"
  cd "$DEPLOY_DIR"
  $COMPOSE_CMD pull
  $COMPOSE_CMD up -d --no-build
  grn "  ✓ 镜像已更新, 服务已重启"
}

step_migrate() {
  hdr "4. 数据库迁移 (alembic upgrade head)"
  # 等 backend 容器至少已创建
  local max_wait=60 elapsed=0
  while [ $elapsed -lt $max_wait ]; do
    if [ -n "$($COMPOSE_CMD ps -q backend 2>/dev/null || true)" ]; then
      break
    fi
    sleep 2; elapsed=$((elapsed + 2))
  done
  $COMPOSE_CMD exec -T backend alembic upgrade head
  grn "  ✓ 数据库迁移完成"
}

step_health_check() {
  hdr "5. 健康检查"
  # 等待 backend 容器 healthy（最多 180s, 与 compose healthcheck start_period 对齐）
  local max_wait=180 elapsed=0
  while [ $elapsed -lt $max_wait ]; do
    local cid h
    cid=$($COMPOSE_CMD ps -q backend 2>/dev/null || echo "")
    if [ -n "$cid" ]; then
      h=$(docker inspect --format='{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")
      if [ "$h" = "healthy" ]; then
        grn "  ✓ backend healthy (等待 ${elapsed}s)"
        break
      fi
      if [ $((elapsed % 15)) -eq 0 ] && [ $elapsed -gt 0 ]; then
        ylw "  等待 backend 就绪(${elapsed}s/${max_wait}s) health=${h}"
      fi
    fi
    sleep 5; elapsed=$((elapsed + 5))
  done
  if [ $elapsed -ge $max_wait ]; then
    red "  ✗ backend 健康检查超时(${max_wait}s)"
    $COMPOSE_CMD logs --tail=30 backend
    exit 1
  fi

  # HTTP 探测
  if curl -sf http://127.0.0.1:18000/api/v1/health >/dev/null 2>&1; then
    grn "  ✓ backend HTTP /api/v1/health 200"
  else
    ylw "  ⚠ backend HTTP 探测未通过(可能仍在启动, 但容器已 healthy)"
  fi
  if curl -sf -o /dev/null http://127.0.0.1:13000/ 2>&1; then
    grn "  ✓ frontend HTTP 响应正常"
  else
    ylw "  ⚠ frontend HTTP 探测未通过"
  fi
}

step_summary() {
  hdr "6. 部署摘要"
  echo "  本次镜像标签: IMAGE_TAG=${IMAGE_TAG}"
  $COMPOSE_CMD ps
  echo ""
  if [ "$IMAGE_TAG" != "prod" ]; then
    ylw "  ⚠ 当前为回滚/指定版本($IMAGE_TAG), 下次推 main 会自动回到 prod"
  fi
  grn "🎉 部署完成!"
}

# ===== 主流程 =====
step_check_prereqs
step_ensure_net
step_login_ghcr
step_verify_image
step_pull_and_up
step_migrate
step_health_check
step_summary
