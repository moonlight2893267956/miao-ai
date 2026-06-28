# Miao AI — 云服务注册清单

> 4 个云服务**供应商侧**的注册步骤。每个都有免费层，个人使用足够。
> 本文档是供应商步骤；填什么值 / 怎么写到 `.env` / 本地 vs 生产分层，看 [`docs/operations/local-dev.md` §2](../docs/operations/local-dev.md) 和 [`docs/operations/deployment.md` §3.1](../docs/operations/deployment.md)。

## 服务一览

| 服务 | 用途 | 免费层额度 | 区域 |
|---|---|---|---|
| [Langfuse Cloud](https://cloud.langfuse.com) | Trace 存储与可视化 | 50k observations/月 | EU / US |
| [Neon](https://neon.tech) | 业务 PostgreSQL | 0.5 GiB + 191.9 compute hrs/月 | AWS 多个 region |
| [腾讯云 COS](https://console.cloud.tencent.com/cos) | 存 agent 代码包 | 50GB 标准存储 + 50GB/月流量 | 国内多 region |
| [DashScope](https://dashscope.console.aliyun.com) | LLM provider（通义千问） | 送体验额度 | 国内 |

---

## 步骤 1：Langfuse Cloud

1. 用 GitHub / Google 账号注册
2. 进默认 Organization → **New Project** 命名 `miao-ai`（生产）/ `miao-ai-dev`（本地）
3. **Settings → API Keys** → **Create new API key** → 拿到 `Public Key` / `Secret Key`

需要保存：
- `LANGFUSE_BASE_URL`（默认 `https://cloud.langfuse.com`）
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `NEXT_PUBLIC_LANGFUSE_PROJECT_ID`（Settings → Project 页面 URL 里 `cmq...` 开头那段）

---

## 步骤 2：Neon

1. 用 GitHub / Google 账号注册
2. **Create Project** 命名 `miao-ai`，选离你近的 region
3. **Branches** → 默认 `main` 是生产分支
4. **Connection Details** → 选 `Connection string` 复制

> 本地 dev 在建好的项目下**再建一个 `dev` 分支**（Settings → Branches → Create Branch）。本地用 dev 分支 URL，生产用 main 分支 URL。这样本地不污染生产数据。

需要保存：
- `DATABASE_URL`（格式：`postgresql://user:pass@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`，写进项目后改前缀为 `postgresql+asyncpg` + `?ssl=require`）

---

## 步骤 3：DashScope（LLM）

1. 阿里云账号注册（需实名）
2. 进 [DashScope 控制台](https://dashscope.console.aliyun.com) → **API-KEY 管理** → **创建新的 API-KEY**
3. 复制 key

需要保存：
- `DASHSCOPE_API_KEY`（格式 `sk-...`）
- `DASHSCOPE_BASE_URL`（默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`）
- `DASHSCOPE_MODEL`（默认 `qwen-plus`）

---

## 步骤 4：腾讯云 COS

1. 注册/登录腾讯云：<https://console.cloud.tencent.com>
2. **实名认证**（个人用户 1~3 分钟）：<https://console.cloud.tencent.com/developer>
3. 开通 COS：<https://console.cloud.tencent.com/cos>（首次会提示开通）
4. **创建存储桶**：
   - COS 控制台 → 存储桶列表 → 创建存储桶
   - 名称：`miao-agents-<APPID>`（bucket 名必须全局唯一，腾讯云会在你账号下自动加 APPID 后缀）
   - 地域：**上海**（ap-shanghai）或**广州**（ap-guangzhou）离你近的
   - 访问权限：**私有读写**
5. **创建 API 密钥**（CAM）：
   - 访问 <https://console.cloud.tencent.com/cam/capi>
   - 弹窗会要求短信验证
   - 点 **新建密钥** → 拿到 `SecretId` 和 `SecretKey`（**只显示一次**，立即复制保存）
6. CORS 配置（写前端时再配，本次不急）

需要保存：
- `TENCENT_SECRET_ID`
- `TENCENT_SECRET_KEY`
- `TENCENT_REGION`（如 `ap-shanghai`）
- `TENCENT_BUCKET`（含 APPID 后缀的完整 bucket 名）
- `COS_ENDPOINT`（`https://cos.<region>.myqcloud.com`，如 `https://cos.ap-shanghai.myqcloud.com`）

---

## 凭证汇总（填到 `.env` 或 `.env.local`）

```bash
# ===== Langfuse Cloud =====
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
NEXT_PUBLIC_LANGFUSE_PROJECT_ID=cmq...

# ===== Neon =====
DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.region.aws.neon.tech/neondb?ssl=require

# ===== 腾讯云 COS =====
TENCENT_SECRET_ID=...
TENCENT_SECRET_KEY=...
TENCENT_REGION=ap-shanghai
TENCENT_BUCKET=miao-agents-1300000000
COS_ENDPOINT=https://cos.ap-shanghai.myqcloud.com

# ===== DashScope（LLM）=====
DASHSCOPE_API_KEY=sk-...
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen-plus
```

> 注意 `DATABASE_URL` 用了 `postgresql+asyncpg` 前缀和 `?ssl=require` —— asyncpg 专用格式（详情 [`docs/operations/troubleshooting.md` §1.1](../docs/operations/troubleshooting.md)）。

---

## 注意事项

- **凭证保密**：所有 `.env` / `.env.local` 都在 `.gitignore` 里，**永远不要 commit**
- **腾讯云 SecretKey**：只显示一次，丢失只能删除重建
- **CORS**：前端直传 COS 时需要在 COS 控制台配 CORS 规则（前端写直传时再配）
- **Bucket 命名**：腾讯云 bucket 必须带 APPID 后缀（控制台会自动加）
- **本地 vs 生产分层**：本地 dev 用 Neon dev 分支 + Langfuse dev project，**不要用生产凭证跑本地**（会自动写生产数据）。详见 [`docs/operations/local-dev.md` §2](../docs/operations/local-dev.md)
