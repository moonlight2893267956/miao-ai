# Miao AI — 云服务注册清单

> 本项目使用四个云服务，**全部有免费层**，对个人使用足够。本机零 Docker 依赖。

## 服务一览

| 服务 | 用途 | 免费层额度 | 区域 |
|---|---|---|---|
| [Langfuse Cloud](https://cloud.langfuse.com) | Trace 存储与可视化 | 50k observations/月 | EU / US |
| [Neon](https://neon.tech) | 业务 PostgreSQL | 0.5 GiB + 191.9 compute hrs/月 | AWS 多个 region |
| [腾讯云 COS](https://console.cloud.tencent.com/cos) | 存 agent 代码包 | 50GB 标准存储 + 50GB/月流量 | 国内多 region |
| [DashScope](https://dashscope.console.aliyun.com) | LLM provider（通义千问） | 送体验额度 | 国内 |

---

## 步骤 1：Langfuse Cloud ✅（已注册）

凭证已经在 `demos/hello-trace/.env` 和根 `.env.example`：
- `LANGFUSE_BASE_URL`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

## 步骤 2：Neon ✅（已注册）

凭证已经在 `demos/hello-trace/.env`：
- `DATABASE_URL`

Phase 1 写 backend 时会用到。

## 步骤 3：DashScope（LLM） ✅（已注册）

已在 `demos/hello-trace/.env`：
- `DASHSCOPE_API_KEY`
- `DASHSCOPE_BASE_URL`
- `DASHSCOPE_MODEL`（默认 `qwen-plus`）

---

## 步骤 4：腾讯云 COS（待注册）

1. 注册/登录腾讯云：<https://console.cloud.tencent.com>
2. **实名认证**（个人用户 1~3 分钟）：<https://console.cloud.tencent.com/developer>
3. 开通 COS：<https://console.cloud.tencent.com/cos>（首次会提示开通）
4. **创建存储桶**：
   - COS 控制台 → 存储桶列表 → 创建存储桶
   - 名称：`miao-agents-<APPID>`（bucket 名必须全局唯一，腾讯云会在你账号下自动加 APPID 后缀；如果你账号 APPID 是 `1300000000`，最终 bucket 叫 `miao-agents-1300000000`）
   - 地域：**上海**（ap-shanghai）或**广州**（ap-guangzhou）离你近的
   - 访问权限：**私有读写**
5. **创建 API 密钥**（CAM）：
   - 访问 <https://console.cloud.tencent.com/cam/capi>
   - 弹窗会要求短信验证
   - 点 **新建密钥** → 拿到 `SecretId` 和 `SecretKey`（**只显示一次**，立即复制保存）
6. CORS 配置（Phase 1+ 写前端时再配，本次不急）

> **保存下来**：
> - `TENCENT_SECRET_ID`
> - `TENCENT_SECRET_KEY`
> - `TENCENT_REGION`（如 `ap-shanghai`）
> - `TENCENT_BUCKET`（含 APPID 后缀的完整 bucket 名）
> - `COS_ENDPOINT`（`https://cos.<region>.myqcloud.com`，如 `https://cos.ap-shanghai.myqcloud.com`）

---

## 凭证汇总（填到根 .env）

```bash
# ===== Langfuse Cloud =====
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...

# ===== Neon =====
DATABASE_URL=postgresql://user:password@ep-xxx.region.aws.neon.tech/miao?sslmode=require

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

---

## 注意事项

- **凭证保密**：所有 `.env` 都在 `.gitignore` 里，**永远不要 commit**
- **腾讯云 SecretKey**：只显示一次，丢失只能删除重建
- **CORS**：前端直传 COS 时需要在 COS 控制台配 CORS 规则（Phase 1+ 再做）
- **Bucket 命名**：腾讯云 bucket 必须带 APPID 后缀（控制台会自动加）
