# 贡献指南

> 给 Miao AI 项目贡献代码 / 文档 / 反馈的规范。
> 新人第一次贡献前先看 [`docs/operations/local-dev.md`](docs/operations/local-dev.md) 跑起来。

## 提交流程

### 1. 分支

| 前缀 | 用途 | 例 |
|---|---|---|
| `feat/` | 新功能 | `feat/agent-version-rollback` |
| `fix/` | bug 修复 | `fix/docker-network-dns` |
| `chore/` | 杂项（依赖、构建、CI） | `chore/bump-pydantic-2.10` |
| `docs/` | 仅文档 | `docs/restructure-into-operations` |
| `refactor/` | 重构（无功能变化） | `refactor/extract-runtime-manager` |
| `test/` | 仅测试 | `test/agent-stop-coverage` |

main 是保护分支，不直接 push。

### 2. 提交前自检

```bash
# 后端
cd backend && uv run pytest
# 前端
cd frontend && pnpm tsc --noEmit
# 类型 / 导入检查
backend/.venv/bin/python -c "from app.main import app; print('OK')"
# 本地启动一遍
bash scripts/stop-all.sh && bash scripts/start-all.sh
# 至少调通 invoke 一次
```

### 3. Commit 规范

**Conventional Commits**：

```
<type>(<scope>): <subject>

<body>

<footer>
```

- **subject**：≤ 50 字，祈使语气，无句号
  - ✅ `feat(agents): add /stop endpoint that preserves DB definition`
  - ❌ `Add stop endpoint.`
- **scope**：`agents` / `runtime` / `api` / `frontend` / `db` / `docs` / `infra` / `auth` / `models`
- **body**：1-3 行说明**动机**，不是"做了什么"
- **footer**：`Refs #123` / `BREAKING CHANGE: ...`

完整 type 列表：`feat` / `fix` / `docs` / `style` / `refactor` / `test` / `chore`

### 4. PR

- 标题用 Conventional Commits 格式（同 commit subject）
- 描述：动机 + 改动 + 截图（UI 改动） + 关联 issue
- 至少 1 个 reviewer

---

## Code Style

### Python

- **类型注解**：公开函数 / 方法 / class 属性**必须**加
- **Docstring**：公开 API 用英文（IDE 友好）
- **Linter**：项目用 ruff（`pyproject.toml` 配）；`uv run ruff check .` 过
- **Formatter**：`uv run ruff format .`
- **Import 顺序**：stdlib → 第三方 → 本地（`from .config import settings`）

### TypeScript

- **类型**：能不加 `any` 就不加；Props 必定义 interface
- **Linter**：`pnpm tsc --noEmit` 通过
- **Style**：用 Next.js 14 默认 ESLint 配置
- **Tailwind**：`cn()` 工具函数合并 className，不要手写字符串拼接

### 文件头

不写 license header（项目小）。新文件加 1 行 docstring / 注释说明用途即可。

### 注释

- 中文 OK（项目主语言中文）
- 解释**为什么**，不是**做了什么**
- TODO 必带 `// TODO(your-name): ...` 或 `# TODO(your-name): ...` 方便后续 grep

---

## 测试要求

| 改动类型 | 必做 |
|---|---|
| 新后端 endpoint | 加 pytest case 或 e2e_smoke.sh 用例 |
| 新前端组件 | 跑 `pnpm tsc --noEmit` + 手动跑通 dev |
| 改 DB schema | 写 alembic migration（`uv run alembic revision --autogenerate -m "..."`） |
| 改 runtime | 至少手验 venv 模式；docker 模式如有修改跑一遍 e2e |
| 改 API 契约 | 同步更新 OpenAPI 注释 + ARCHITECTURE §5 |

---

## 文档同步规则

> **架构文档同步更新**：任何涉及架构调整的代码变更，必须同步更新 `docs/ARCHITECTURE.md`。
> 不涉及架构的改动（纯 bug fix、样式调整、日志优化等）无需更新。

| 改动 | 同步更新 |
|---|---|
| 新增 / 删除 API 端点 | `docs/ARCHITECTURE.md` §5 + `docs/api-reference.md`（如有） |
| DB schema 变更（字段、列类型、表） | `docs/ARCHITECTURE.md` §4 + 写 alembic migration |
| 运行时变化（新增模块、改进程生命周期） | `docs/ARCHITECTURE.md` §6 |
| 配置项新增 / 默认值变更 | `docs/ARCHITECTURE.md` §9 + `.env.example` |
| 前端路由或核心组件调整 | `docs/ARCHITECTURE.md` §7 + `docs/design/ui-design-system.md`（如改 token） |
| 外部服务依赖变更 | `docs/ARCHITECTURE.md` §10 + `docs/operations/deployment.md`（如影响部署） |
| 部署流程变化 | `docs/operations/deployment.md` |
| 本地启动流程变化 | `docs/operations/local-dev.md` |
| 新坑 / 修法 | `docs/operations/troubleshooting.md` |
| 凭证 / 加密 / 授权变化 | `docs/operations/security.md` |
| 监控 / 日志 / 健康探针变化 | `docs/operations/monitoring.md` |

---

## 安全规范

- 任何凭证（`DASHSCOPE_*` / `LANGFUSE_*` / `COS_*` / `DATABASE_URL` / `ENCRYPTION_KEY`）**不得** commit
- `.env` / `.env.local` 已在 `.gitignore`，但 commit 前**一定**跑 `git status --short` 扫一遍
- 改加密逻辑 → 必更新 `docs/operations/security.md` + 通知 reviewer
- 写新 agent 模板 → 输入校验用 Pydantic；**不信任用户上传的代码**
- 部署前**必须** `grep -r "sk-\|pk-\|secret\|password" . --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git` 自检

---

## 沟通

- 日常沟通：项目内 IM
- Bug / Feature Request：GitHub Issues
- 部署事故：先在群里 at 所有人 → 写 postmortem 进 `docs/history/`

---

## 第一次贡献 Checklist

- [ ] 跑通 [`docs/operations/local-dev.md`](docs/operations/local-dev.md) 全文
- [ ] 读 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 至少前 3 章
- [ ] clone 仓库，搭本地 dev 环境
- [ ] 找一个小改动（`good first issue` 标签或文档 typo）练手
- [ ] 提交 PR，等 review
