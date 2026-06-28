# 安全规范

> 凭证管理、加密策略、认证授权、已知风险。
> 改加密 / 凭证加载 / 授权逻辑前**必读**。

---

## 1. 凭证管理

### 1.1 凭证分类

| 类 | 变量 | 重要度 |
|---|---|---|
| 数据库 | `DATABASE_URL` | 高（含密码） |
| Trace | `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | 中（公开 key，secret 是写权限） |
| 对象存储 | `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` / `TENCENT_BUCKET` / `TENCENT_REGION` / `COS_ENDPOINT` | 高 |
| LLM | `DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` / `DASHSCOPE_MODEL` | 高 |
| 对称加密 | `ENCRYPTION_KEY` | **关键**（丢了 = 已加密数据全废） |

### 1.2 文件分层

| 文件 | 用途 | git | 适用 |
|---|---|---|---|
| `.env.example` | 模板（空值） | **跟踪** | 参考 |
| `.env` | 生产凭证 | **ignore** | 服务器 |
| `.env.local` | 本地凭证 | **ignore** | 本地 dev |

加载顺序：`os.environ` > `.env.local` > `.env`（pydantic-settings v2 后者覆盖前者）。

详见 [`docs/operations/local-dev.md` §2](local-dev.md) 和 `backend/app/config.py`。

### 1.3 备份要求

- **`.env`** 必须备份到密码管理器（1Password / Bitwarden）
- **`ENCRYPTION_KEY`** 单独备份（跟其他凭证隔离）
- 任何凭证**不得** 出现在：
  - git commit / PR 描述 / commit message
  - Slack / IM / 邮件
  - 文档示例（占位符用 `pk-lf-xxxxxxxx-...`）
  - 日志（注意 `print(os.environ)` 这种 bug）

### 1.4 轮换

| 凭证 | 轮换难度 | 步骤 |
|---|---|---|
| Neon 密码 | 简单 | Neon 控制台重置 → 改 `.env` → 重启 backend |
| Langfuse key | 简单 | Langfuse 控制台新建 → 改 `.env` → 重启 |
| COS key | 中 | 腾讯云控制台新建 → 改 `.env` → 验证上传/下载 |
| DashScope key | 简单 | 阿里云控制台新建 → 改 `.env` → 验证 invoke |
| `ENCRYPTION_KEY` | **难** | 见 §2.3 |

---

## 2. Provider API Key 加密

### 2.1 算法

- **Fernet**（AES-128-CBC + HMAC-SHA256）
- 实现：`backend/app/security/crypto.py` 的 `encrypt_value / decrypt_value`
- Key：32 字节 URL-safe base64

### 2.2 存储

- 明文 API key **绝不**进 DB
- 加密后存 `model_providers.api_key_encrypted`（`String` 类型，base64 文本）
- 解密只在 agent 子进程启动时（运行时一次性），**不写日志**

### 2.3 ENCRYPTION_KEY 轮换（**重要**）

换了 `ENCRYPTION_KEY` 后，旧 key 加密的数据解不开。**必须**同步重加密：

```python
# 1. 新 key 写 .env（或临时环境变量）
# 2. 跑脚本重加密（待写：backend/scripts/rotate_encryption.py）：
#    - 读 model_providers 全表
#    - decrypt_value(old_key, encrypted) → plain
#    - encrypt_value(new_key, plain) → new_encrypted
#    - 写回 DB（事务包裹）
# 3. 验证：重启 backend → invoke 一次
# 4. 备份旧 key（保留 7 天）→ 删
```

**未实现自动化**（6/28）。当前如果换 key 需要手动跑 SQL + 脚本。

### 2.4 备份策略

- `ENCRYPTION_KEY` 单独备份，**不跟 `.env` 放一起**
- 至少 2 个独立位置（密码管理器 + 加密 U 盘）

---

## 3. 认证 / 授权

### 3.1 登录

- 密码：bcrypt 哈希存 `users.password`
- Session：HttpOnly cookie，存 `user_sessions` 表
- 默认 7 天有效

### 3.2 写操作鉴权

- 路由用 `dependencies=login_required` 装饰（见 `backend/app/main.py`）
- 例外：`/auth/login` / `/health` / `/health/ready`

### 3.3 Agent API Key

- 格式：`miao_<random>`（Fernet 风格）
- 明文**只在创建时返回一次**
- 存 `api_keys.key_hash`（bcrypt 哈希）
- 撤销：设 `revoked_at`（不删行，留 audit trail）

### 3.4 已知缺口

- **没 CSRF 保护**：session cookie 是 SameSite=Lax（默认），减风险但不是 0
- **没 rate limit**：invoke 端点可被滥用
- **admin 用户管理**：单用户，靠 DB 改 `users.password`，没 admin API
- **密码强度**：明文存（早期决策），如要改需评估迁移

---

## 4. CORS

- 配置：`backend/app/main.py` `CORSMiddleware`
- 当前 allow_origins：
  - `http://localhost:3000`（本地 dev）
  - `https://agent.yunmiao.site`（生产）
- 加新源必须改后端 + 重启

---

## 5. 已知风险

### 5.1 凭证泄露历史

**2026-06-27**：生产 `.env` 全文曾在 AI 助手对话日志里出现（包括 ENCRYPTION_KEY / COS / Neon / Langfuse / DashScope 全部）。

**建议立即轮换**（按优先级）：
1. **DASHSCOPE_API_KEY**（生产 LLM 调用计费）— 阿里云控制台新建
2. **TENCENT_SECRET_KEY**（COS 完整读写权限）— 腾讯云 CAM 重建
3. **LANGFUSE_SECRET_KEY**（trace 写权限）— Langfuse 控制台新建
4. **DATABASE_URL**（DB 全权限）— Neon 重置密码
5. **ENCRYPTION_KEY**（难，需 §2.3 流程）

如果轮换，**先轮换高风险（DASHSCOPE / COS）**，再轮换 Langfuse，最后 DB / ENCRYPTION_KEY。

### 5.2 部署机器访问

- 服务器 root 密码：未审计
- SSH：未审计是否禁密码登录
- 服务器本地 7890 端口代理：信任 LAN 访问（如果服务器在云上，注意安全组）

### 5.3 agent 上传代码

- 用户上传的 zip 在 backend 解压 → 装依赖 → 跑 `agent.py`
- **等于在 backend 容器里跑用户代码**
- 风险：恶意 agent 可以读 backend 环境变量（含所有 .env 变量）
- 缓解：v0.3.0 还没做沙箱（计划进 Phase 3）

---

## 6. 安全 Checklist（部署前）

- [ ] `.env` 备份到密码管理器
- [ ] `ENCRYPTION_KEY` 单独备份
- [ ] 服务器 SSH 禁密码登录（用 SSH key）
- [ ] 服务器 `sudo` 限授权用户
- [ ] 服务器 Docker socket 限权（不能让 agent 容器跑 docker 命令——v0.3.0 还没隔离）
- [ ] 日志无敏感信息（grep `print(.*env.*)` / `logger.*env.*`）
- [ ] CORS 不允许通配 `*`
- [ ] 监控异常登录（连续失败 5 次告警——v0.3.0 未实现）
- [ ] `curl` 验证 `/api/v1/auth/me` 未登录返回 401
- [ ] `curl` 验证 `/api/v1/agents` 未登录返回 401
