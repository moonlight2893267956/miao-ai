# Miao AI Frontend

Next.js 14 + Tailwind + 自写 shadcn 风格组件。管理 Agent + 看 trace + 试运行。

## 当前进度

- ✅ **Phase 1 前端** — Agent 列表 / 详情（版本管理 + Key 管理 + 试运行）/ Traces 页面
- ⏳ 后续 Phase

## 开发

```bash
cd frontend
pnpm install  # 第一次需要
pnpm dev      # http://localhost:3000
```

要求 Node ≥ 18.17（推荐 20+）。项目用了 Node 22。

## 配置

`.env.local`（默认）：
```
NEXT_PUBLIC_API_BASE=http://localhost:8000
NEXT_PUBLIC_LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

## 页面

| 路径 | 功能 |
|---|---|
| `/` | 重定向到 `/agents` |
| `/agents` | Agent 列表（创建/删除，实时 status） |
| `/agents/[name]` | 详情：版本管理（上传/激活）+ API Key 管理 + 在线试运行 |
| `/traces` | iframe 嵌 Langfuse Cloud，可按 agent 名过滤 |

## 目录

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx          # 顶栏导航
│   │   ├── page.tsx            # → /agents
│   │   ├── agents/
│   │   │   ├── page.tsx        # 列表
│   │   │   └── [name]/page.tsx # 详情
│   │   ├── traces/page.tsx     # Langfuse iframe
│   │   └── globals.css         # Tailwind + shadcn 风格变量
│   ├── components/ui/          # Button / Card / Input / Label / Textarea / Badge
│   └── lib/
│       ├── api.ts              # 后端 API 封装
│       └── utils.ts            # cn()
├── package.json
└── ...
```

## 跑通流程

1. 启动 backend（<http://localhost:8000>）
2. 启动 frontend（<http://localhost:3000>）
3. 在 `/agents` 创建 agent
4. 进详情 → 上传 zip → Activate
5. 创建 API key → 在试运行区粘贴 → Run
6. 看到 output + trace_id，点链接去 Langfuse Cloud 看
