# Miao AI — UI Design System v2.0

> **设计师**: UI Designer | **日期**: 2026-06-24 | **状态**: 设计提案

---

## 1. 设计理念

### 品牌方向: "Warm Tech Meets Playful Precision"

Miao AI 是一个自托管的 AI Agent 平台，面向开发者与 DevOps 工程师。设计需要在 **技术专业感** 和 **品牌亲和力** 之间找到平衡。

| 维度 | 设计决策 |
|------|---------|
| **目标用户** | AI 应用开发者、DevOps 工程师 |
| **使用场景** | 部署管理 AI agent、监控调用、查看 trace |
| **品牌调性** | 温暖、精准、略带俏皮（"Miao" = 喵） |
| **差异化** | 告别通用 AI 工具蓝紫暗色审美，用暖色调 + 猫主题微交互塑造灵魂 |

### 核心美学原则

1. **大胆而不喧嚣** — 用独特的紫罗兰 + 琥珀渐变替代通用 AI 配色
2. **意图明确的层次** — 通过字体对比、间距节奏、颜色权重建立清晰视觉流
3. **不让用户思考** — 每个操作都有清晰反馈，空状态有教育引导
4. **WCAG AA 达标** — 所有文字/组件对比度 ≥ 4.5:1 / 3:1

---

## 2. 设计令牌 (Design Tokens)

### 2.1 色彩系统 (OKLCH 色域)

**为什么用 OKLCH？** 感知均匀，同亮度不同色相的视觉亮度一致，不受 HSL 的亮度不均匀问题困扰。

#### 品牌色

| Token | 色值 | 用途 |
|-------|------|------|
| `--brand-violet-500` | `oklch(55% 0.170 276)` | 主品牌色，CTA 按钮、链接 |
| `--brand-violet-600` | `oklch(45% 0.180 275)` | 按钮 hover、深色强调 |
| `--brand-violet-300` | `oklch(78% 0.085 280)` | 暗色模式下主色 |
| `--brand-amber-400` | `oklch(74% 0.150 75)` | 强调色、通知徽章、渐变点缀 |
| `--brand-amber-300` | `oklch(80% 0.125 78)` | 暗色模式强调色 |

#### 中性色 (带品牌色微色调)

不使用纯灰（`oklch(50% 0 0)`），所有中性色加入微量紫罗兰色相（chroma 0.005-0.012），实现潜意识色彩协调。

| 用途 | Light | Dark |
|------|-------|------|
| 主背景 | `oklch(100% 0.001 285)` | `oklch(6% 0.008 285)` |
| 次背景 | `oklch(98% 0.003 285)` | `oklch(10% 0.012 285)` |
| 卡片/浮层 | `white` | `oklch(18% 0.012 285)` |
| 主文字 | `oklch(18% 0.012 285)` | `oklch(90% 0.007 285)` |
| 次要文字 | `oklch(52% 0.010 285)` | `oklch(68% 0.010 285)` |

#### 语义色

| 语义 | 背景色(Light) | 文字色 |
|------|-------------|--------|
| Success | `oklch(93% 0.045 160)` | `oklch(48% 0.135 155)` |
| Error | `oklch(94% 0.025 20)` | `oklch(45% 0.165 12)` |
| Warning | `oklch(94% 0.050 85)` | `oklch(55% 0.120 78)` |
| Info | `oklch(93% 0.035 250)` | `oklch(48% 0.115 250)` |

#### 60-30-10 分配

- **60%**: 中性背景、留白
- **30%**: 文字、边框、非激活状态
- **10%**: 品牌色作为 CTA、琥珀作为强调

---

### 2.2 排版系统

| 层级 | 字体 | 大小 | 字重 | 用途 |
|------|------|------|------|------|
| Display | Outfit | 3.25rem | 800 | 首页 hero（如有） |
| H1 | Outfit | 2.25rem | 700 | 页面标题 |
| H2 | Outfit | 1.75rem | 700 | 区块标题 |
| H3 | Outfit | 1.375rem | 600 | 卡片标题 |
| Body | Outfit | 0.9375rem | 400 | 正文 |
| Body-Small | Outfit | 0.8125rem | 400 | 辅助信息 |
| Caption | Outfit | 0.75rem | 500 | 标签、徽章 |
| Code | JetBrains Mono | 0.8125rem | 400 | 代码、trace ID |

**字体选择理由**:
- **Outfit** 替代 Inter/Roboto — 保持几何现代感的同时有更高的辨识度
- **JetBrains Mono** — 专为代码设计的等宽字体，连字特性优秀
- 只用了 2 个字体族，减少加载体积

---

### 2.3 间距系统 (8px 基准)

```
4px  → --space-1  (最小间距)
8px  → --space-2
12px → --space-3  (组件内间距)
16px → --space-4  (标准间距)
20px → --space-5
24px → --space-6  (区块间距)
32px → --space-8  (章节间距)
48px → --space-12
64px → --space-16
```

所有垂直间距基于行高倍数 (1.55 × 15px ≈ 23.25px)，确保垂直节奏一致。

---

### 2.4 动效系统

| 用途 | 缓动函数 | 时长 |
|------|---------|------|
| 微交互 (hover/focus) | `cubic-bezier(0.25, 1, 0.5, 1)` | 150ms |
| 页面切换/展开 | `cubic-bezier(0.16, 1, 0.3, 1)` | 250ms |
| 弹跳效果 | `cubic-bezier(0.34, 1.56, 0.64, 1)` | 400ms |

**关键规则**: 永远不动画化布局属性 (width/height/top/left)，只动画化 transform 和 opacity。

---

### 2.5 阴影系统

| Token | 值 | 用途 |
|-------|---|------|
| `--shadow-xs` | `0 1px 2px oklch(10% 0.01 285 / 0.04)` | 微妙层次 |
| `--shadow-sm` | 双层组合 | 卡片默认 |
| `--shadow-md` | 多层组合 | 卡片 hover |
| `--shadow-lg` | 更大扩散 | 弹窗/下拉 |
| `--shadow-glow-violet` | `0 0 20px oklch(55% 0.17 276 / 0.25)` | 品牌光晕 |
| `--shadow-glow-amber` | `0 0 20px oklch(68% 0.155 72 / 0.2)` | 强调光晕 |

**暗色模式**: 不使用 shadow 表达层级，改用**更亮的表面色** (`surface-1` → `surface-2` → `surface-3`)。

---

## 3. 布局架构

### 3.1 App Shell

```
┌──────────┬──────────────────────────────────────┐
│          │  Header (56px)                        │
│ Sidebar  │  ┌─ breadcrumb ────┬─ stats ──────┐  │
│ (240px)  │  └─────────────────┴───────────────┘  │
│          │                                       │
│ ┌──────┐ │  Page Content                         │
│ │ Logo │ │  ┌─────────────────────────────────┐  │
│ └──────┘ │  │                                 │  │
│          │  │  主内容区 (max-w: 1280px)        │  │
│ Nav      │  │                                 │  │
│ ───────  │  └─────────────────────────────────┘  │
│ Overview │                                       │
│ Agents   │                                       │
│ Traces   │                                       │
│ Settings │                                       │
│          │                                       │
│ ───────  │                                       │
│ Theme    │                                       │
└──────────┴───────────────────────────────────────┘
```

- **Sidebar**: 固定 240px，移动端收起到 64px (仅图标)
- **Header**: 固定 56px，面包屑 + 全局状态
- **Content**: 最大宽度 1280px，自适应 padding

### 3.2 断点策略

| 断点 | 宽度 | Sidebar | 网格 |
|------|------|---------|------|
| Mobile | < 768px | 64px (仅图标) | 1 列 |
| Tablet | 768-1024px | 240px | 2 列 |
| Desktop | ≥ 1024px | 240px | 3-4 列 |

---

## 4. 组件架构

### 4.1 导航组件

```
Sidebar
├── Logo + Brand (猫耳 SVG 图标)
├── NavSection "概览"
│   └── NavItem "Dashboard"
├── NavSection "Agent 管理"
│   ├── NavItem "Agents" (+ Badge 计数)
│   └── NavItem "Agent Detail"
├── NavSection "可观测"
│   └── NavItem "Traces"
├── NavSection "设置"
│   └── NavItem "Settings"
└── Footer
    ├── ThemeToggle
    └── Version
```

**NavItem 状态**:
- `default`: 灰色文字
- `hover`: 浅色背景 + 深色文字
- `active`: 品牌色文字 + 品牌色浅背景 + 左侧 3px 指示条

### 4.2 Agent 卡片

```
┌──────────────────────────────────┐
│ [Avatar]  Name              [Status] │
│           Description...            │
│                                     │
│ ─────────────────────────────────── │
│ v2             127 calls · 99.9%   │
└──────────────────────────────────┘
```

**交互**: hover 上浮 2px + 品牌色边框 + 顶部渐变条出现

### 4.3 Status Badge

| 状态 | 样式 |
|------|------|
| Running | 绿色背景 + 脉冲动画圆点 |
| Building | 琥珀色背景 + 快脉冲圆点 |
| Crashed | 红色背景 + 静态圆点 |
| Stopped | 灰色背景 + 静态圆点 |
| Idle | 蓝色背景 + 静态圆点 |

### 4.4 版本列表

```
┌──────────────────────────────────────────────┐
│ [ACTIVE] v2  entrypoint:agent:invoke  [Running] │
├──────────────────────────────────────────────┤
│ v1          entrypoint:agent:invoke  [Activate] │
└──────────────────────────────────────────────┘
```

### 4.5 API Key 管理

- **新 Key 揭示**: 渐变背景卡片，醒目提示"仅显示一次"
- **已有 Key**: 列表展示 label + 创建时间 + 吊销按钮

### 4.6 Try Run 面板

- 支持普通调用和 SSE 流式切换
- 流式输出时显示**闪烁光标动画**
- 输出区域显示 trace_id + 耗时 + token 数

---

## 5. 页面规划

### 5.1 Dashboard (新增)

| 区域 | 内容 |
|------|------|
| 统计卡片 | Total Agents, Running, Invocations, Versions |
| 最近 Agent | Agent 卡片列表 |

### 5.2 Agents 列表

| 区域 | 内容 |
|------|------|
| 操作栏 | 搜索框 + 刷新 + 新建按钮 |
| Agent 网格 | 卡片式列表，hover 时显示操作 |
| 空状态 | "还没有 agent" + 创建引导 |

### 5.3 Agent 详情 (Tab 化改造)

| Tab | 内容 |
|-----|------|
| Versions | 版本历史 + 上传 + 激活 |
| API Keys | 创建/吊销 key |
| Try Run | 输入 JSON → invoke/stream → 看结果 |

### 5.4 Traces

| 区域 | 内容 |
|------|------|
| 筛选面板 | Agent name 过滤 + 跳转 Langfuse |
| 提示卡片 | Trace 使用说明 |

---

## 6. 主题支持

### 暗色模式特殊处理

| Light | Dark |
|-------|------|
| 用阴影表达层级 | 用更亮表面色表达层级 |
| 深色文字 + 白色背景 | 浅色文字 + 深灰背景 |
| 正常字重 400 | 减轻到 350-380 |
| 鲜艳强调色 | 略微降低饱和度 |

通过 `[data-theme="dark"]` 选择器切换，用户偏好记忆在 localStorage。

---

## 7. 可访问性

- ✅ 所有文字对比度 ≥ 4.5:1 (WCAG AA)
- ✅ 所有交互元素有 focus-visible 样式
- ✅ 状态信息不单独依赖颜色（有文字 + 图标双重编码）
- ✅ 支持 `prefers-reduced-motion` 关闭动效
- ✅ 所有字体大小使用 rem（尊重用户浏览器缩放设置）
- ✅ 触摸目标 ≥ 44px

---

## 8. 实施路线图

### Phase 1: 设计令牌迁移
1. 替换 `globals.css` 中的 HSL 变量为 OKLCH 变量
2. 引入 Outfit + JetBrains Mono 字体
3. 添加暗色模式 token 映射

### Phase 2: 布局重构
1. 实现 Sidebar + Header 布局
2. 创建 Dashboard 页面
3. 改造 Agents 页面卡片样式

### Phase 3: 组件升级
1. 升级 Status Badge（加脉冲动画）
2. 改造 Agent Detail 为 Tab 结构
3. 美化 Try Run 面板（流式光标动画）

### Phase 4: 细节打磨
1. 空状态插图/引导
2. 加载骨架屏
3. 键盘导航优化
4. 最终一致性审查

---

## 与现有代码的兼容性

当前项目使用 **shadcn/ui (Radix UI) + Tailwind CSS**，设计方案完全兼容：

- Tailwind 可以直接使用设计令牌（CSS 变量映射到 Tailwind config）
- shadcn/ui 组件的样式可以通过 CSS 变量覆盖
- 布局改动在 `layout.tsx` 中完成
- 页面内容改动在各 page.tsx 中完成

---

> **原型预览**: 打开 `frontend/design-system/miao-ai-design-system.html` 查看交互原型
> **设计令牌源码**: 原型文件中的 `:root` / `[data-theme="dark"]` CSS 块可直接迁移
