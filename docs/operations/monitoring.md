# 监控与日志

> 怎么找日志、查健康、debug 常见监控问题。
> 详细排错看 [`docs/operations/troubleshooting.md`](troubleshooting.md)。

---

## 1. 日志位置

### 1.1 本地 dev

| 服务 | 位置 |
|---|---|
| backend 主进程 | `/tmp/miao-backend.log` |
| frontend 主进程 | `/tmp/miao-frontend.log` |
| backend pid | `/tmp/miao-backend.pid` |
| frontend pid | `/tmp/miao-frontend.pid` |
| agent 子进程（venv） | `backend/logs/agents/{name}.log`（如配置） |
| agent 子进程 stdout/stderr | 通过 backend 转发到 `/tmp/miao-backend.log` |

### 1.2 生产（docker）

| 服务 | 位置 |
|---|---|
| backend 主进程 | `docker logs miao-backend` |
| frontend 主进程 | `docker logs miao-frontend` |
| agent 子进程 | `docker logs miao-{name}` |
| backend 内文件 | `docker exec miao-backend ls /tmp/miao/` |

### 1.3 日志格式

- 结构化 key=value：`miao.recovery.ok agent=qwen-chat port=9103`
- 用 `grep` / `jq` 解析（不是 JSON 格式）
- 时间戳是本地时区

---

## 2. 健康探针

### 2.1 进程级

| 探针 | 端点 | 检查 |
|---|---|---|
| liveness | `GET /api/v1/health` | 进程在 |
| readiness | `GET /api/v1/health/ready` | 进程在 + DB 可达 |
| docker healthcheck | `docker inspect --format '{{.State.Health.Status}}' miao-backend` | 连续 3 次 200 |
| 端到端 | `curl https://agent.yunmiao.site/api/v1/health` | 公网通 |

### 2.2 失败表现

- 进程崩 → docker 自动 restart（`restart: unless-stopped`）
- DB 不可达 → `/api/v1/health/ready` 500
- healthcheck SyntaxError → 容器 `unhealthy` 但 HTTP 200（误报，看 [`deployment.md` §5](deployment.md)）

---

## 3. 关键日志关键词

用 `grep` 快速定位问题：

```bash
# Recovery（启动时拉起 agent）
grep "miao.recovery" /tmp/miao-backend.log

# Watchdog（空闲自动 stop）
grep "miao.watchdog" /tmp/miao-backend.log

# Agent 生命周期
grep "miao.runtime" /tmp/miao-backend.log

# Trace 上报
grep "miao.trace" /tmp/miao-backend.log

# 错误
grep -E "ERROR|Traceback" /tmp/miao-backend.log
```

**`miao.recovery.ok`**：启动成功
**`miao.recovery.failed`**：启动失败，看 error 字段
**`miao.watchdog.idle_stop`**：空闲自动 stop
**`miao.watchdog.try_restart`**：watchdog 重启
**`miao.trace.sent`**：trace 成功上报
**`miao.trace.failed`**：trace 上报失败（不影响 invoke 返回）

---

## 4. Langfuse 追踪

### 4.1 项目

| 环境 | Project | 用途 |
|---|---|---|
| 生产 | `miao-ai` | 服务器 invoke |
| 本地 | `miao-ai-dev` | `bash scripts/start-all.sh` |

### 4.2 检索 trace

- 通过 `session_id`（在 invoke body 显式传）
- 通过 `tags`（如 `["dev", "isolation-test"]`）
- 通过 `user_id`（在 metadata）
- 通过 `trace_id`（响应里有）

### 4.3 失败模式

- trace 失败不阻塞 invoke（try/except 包了）
- 偶发断流：Langfuse 5xx，正常
- 持续断流：检查 `LANGFUSE_*` env + 网络

---

## 5. 进程 / 容器状态

### 5.1 本地

```bash
bash scripts/status.sh
# 显示 backend / frontend pid + 健康状态
ps -p $(cat /tmp/miao-backend.pid)
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

### 5.2 生产

```bash
ssh yunmiao@yunmiao.site
cd ~/apps/miao-ai
docker compose -f docker-compose.prod.yml ps
docker ps -a | grep miao-
```

---

## 6. 数据库监控

```bash
# 行数快速看
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
psql "$DATABASE_URL" -c "
SELECT relname, n_live_tup
FROM pg_stat_user_tables
WHERE schemaname='public'
ORDER BY relname;
"

# 长查询
psql "$DATABASE_URL" -c "
SELECT pid, state, query_start, left(query, 80)
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY query_start;
"

# 锁
psql "$DATABASE_URL" -c "
SELECT relation::regclass, mode, granted, pid
FROM pg_locks
WHERE relation IS NOT NULL;
"
```

---

## 7. 告警（未实现，建议做）

| 触发 | 通知 |
|---|---|
| backend 进程退出 | systemd auto-restart（已配） + 邮件 |
| backend healthcheck 持续 unhealthy 5min | 短信 |
| `miao.recovery.failed` 出现 | 邮件 |
| `miao.watchdog.try_restart` 5min 内出现 3+ 次 | 邮件 |
| Langfuse 24h 无新 trace | 邮件 |
| `LANGFUSE_SECRET_KEY` 失效（401 错误） | 邮件 |
| Neon 连接失败 5min | 邮件 |
| COS 上传/下载 5xx 持续 5min | 邮件 |
| 服务器磁盘 > 80% | 短信 |
| 服务器内存 > 90% | 邮件 |

**当前实现状态**：**0**。所有告警靠人工看日志。
**v0.4+ 计划**：接入 Langfuse webhook + 自建 simple alert（cron 跑 healthcheck script）。

---

## 8. 性能基线（v0.3.0 6/27 实测）

| 操作 | 期望 | 实测 |
|---|---|---|
| `/api/v1/health` | < 50ms | ~5ms |
| `/api/v1/health/ready` | < 200ms | ~50ms |
| invoke（agent 已 running，venv 模式） | < 3s | ~2.7s |
| invoke（agent stopped，cold start，venv 模式） | < 10s | ~5s |
| invoke（agent stopped，docker 模式 + build） | < 5min | ~1-2min（首次） |
| invoke（agent stopped，docker 模式 + image 缓存） | < 15s | ~5-10s |
| 登录 | < 500ms | ~50ms |
| list agents | < 200ms | ~30ms |
| list models | < 200ms | ~30ms |
| create API key | < 200ms | ~30ms |

> v0.3.0 没做压测，以上是单实例本地 dev 实测，**仅供参考**。

---

## 9. 备份

| 数据 | 备份策略 |
|---|---|
| 业务 DB（Neon） | Neon 自动备份（按 plan） + 重要 migration 前手动 dump |
| `.env` | 密码管理器（人工） |
| agent 代码包 | COS（自动 3 副本）+ 不删策略 |
| Langfuse trace | Langfuse 平台自带（按 plan） |
| 监控日志 | 当前不备份（重启会丢） |

---

## 10. 故障演练（建议每季度做一次）

- [ ] 杀掉 backend → docker 1s 内自动 restart
- [ ] 停掉 Neon → backend `/health/ready` 500 → 告警
- [ ] 改坏 `ENCRYPTION_KEY` 重启 → 启动后 invoke 报 Fernet 解密错误
- [ ] 把 `LANGFUSE_PUBLIC_KEY` 改错 → trace 报错但 invoke 不挂
- [ ] CORS 拒绝新源 → 浏览器 console 报 CORS
- [ ] `.env` 删了 → 启动失败，错误信息明确
