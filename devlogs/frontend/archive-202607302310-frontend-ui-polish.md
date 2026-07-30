# 前端全站美化与优化

**日期：** 2026-07-30
**目标：** 全站视觉打磨 + 重复模式组件化 + 性能优化 + 主题细节修复，系统性解决 inline style 无 hover/focus 反馈、原子组件缺失导致的 7-8 份按钮拷贝、PyramidPage 硬编码色/无移动端适配/无错误态、TopBar 标题漂移等债务。

---

## 背景

TimeTrace 前端（`frontend/`，React 19 + Vite + Tailwind 4 名义引入但实际纯 inline style）的 design token 体系（`src/index.css` 双主题 `--bg-*/--text-*/--accent/--cat-*`）和 RecordDetailPanel、CategoryFilter、ReportView 等少数组件质量很高，但存在三个系统性短板：

1. **inline style 写不了 `:hover`/`:focus`** → 全站按钮、chip、输入框零 hover 反馈
2. **`components/ui/` 目录只有 Markdown.tsx 和 ThemeToggle.tsx** — 没有 Button/Input/Chip/Section/EmptyState 等基础组件，导致同样的按钮样式在 7-10 处重复手写、参数互相漂移
3. **实际问题**：TopBar TITLES 只覆盖 5/8 路由（/pyramid /audit /llm-log 标题错显"时间轴"）、`outline:'none'` 杀死焦点环、AgentSidebar 空对话无法删除、TokenCreatedDialog 点遮罩永失 secret、`var(--font-mono)` 引用不存在的变量等

用户要求：全站范围、四个方向（视觉打磨 + 重复模式组件化 + 性能优化 + 主题细节）全做。

---

## 操作步骤

### 1. 两路并行代码探索

派两个 Explore agent 分头扫描全站 30+ 个源文件产出结构化清单（逐文件定位问题 + 建议 + 价值分级），最终两份报告交叉验证、结论一致：

- **最高杠杆**：按钮/输入框从 inline style 迁到 CSS class（一次性解锁 hover/focus/disabled 三态）
- **最大重复**：主渐变按钮 7 份、幽灵按钮 8 份、surface 卡片 6+ 份、节标题 5 份、soft pill 4-5 份（padding/dot-size 漂移）
- **最严重页面**：PyramidPage（硬编码分类色 + 无响应式 + 无 error 态）、LlmLogPage（硬编码 caller 色 + 加载态纯文字）

### 2. Phase A — `index.css` 新增 token 与 `.tt-*` 组件类

**新增 design token：**

| Token | dark 值 | light 值 | 替换目标 |
|---|---|---|---|
| `--scrim` | `rgba(4,6,12,0.6)` | `rgba(30,35,60,0.35)` | 4 处 `rgba(0,0,0,0.5)` 硬编码遮罩 |
| `--font-mono` | `'JetBrains Mono','Consolas','Fira Code',ui-monospace,monospace` | 同 | 4 套不一致的等宽字体栈 |

**新增 `.tt-*` 组件类（`index.css` 末尾，带 `:hover`/`:focus-visible`/`:disabled`/`[data-active]` 态，全部读 design token）：**

- `.tt-btn-primary` — grad-accent 底 + `var(--accent-contrast)` 字 + shadow-glow；hover 提亮
- `.tt-btn-ghost` — bg-surface + 1px border；hover 换 `--bg-hover`；`[data-active]` 改 accent 色调
- `.tt-btn-danger` — ghost 语言但 hover 变 error-bg + error-tinted border
- `.tt-btn-icon` — 34×34 方形图标按钮
- `.tt-input` — bg-raised + border；focus 时 accent 边框 + `0 0 0 3px var(--accent-subtle)` 光晕（不再靠散落 `outline:'none'` 杀死焦点环）
- `.tt-chip` — `color-mix` 软色 pill，支持 `--chip-color` 自定义色调、`button.tt-chip` 可点击筛选变体、`.tt-chip-solid` 实心强调变体
- `.tt-card` — bg-surface + border + radius-lg + shadow-sm
- `.sr-only` — 全局一条（删 4 份手写对象拷贝）
- `.tt-skeleton` — shimmer 渐变动画，`prefers-reduced-motion: reduce` 时停动画

### 3. Phase B — `components/ui/` 原子组件

创建 7 个文件共 10 个组件，thin wrapper 套 `.tt-*` class：

| 文件 | 导出组件 | 消灭的重复 |
|---|---|---|
| `Button.tsx` | `Button`（variant: primary/ghost/danger, loading, active） | 主按钮 7 份 + 幽灵按钮 8 份 |
| `IconButton.tsx` | `IconButton`（强制 `aria-label`） | 图标按钮多份 + 缺失 aria-label |
| `Chip.tsx` | `Chip`（color/solid/dot） | StatusChip/CategoryBadge/FlagChips/TurnView/verdict 5 种 pill |
| `Card.tsx` | `Card`、`Section`（title+desc）、`KV` | 卡片 6+ 份 + 节标题 5 份 |
| `Feedback.tsx` | `EmptyState`（mascot 可选）、`ErrorBanner`（role="alert" + 重试）、`LiveBadge` | mascot 空态 2 份 + 纯文字 3 份、错误横幅 3-4 份、圆点徽章 2 份 |
| `Skeleton.tsx` | `Skeleton`、`SkeletonRows` | 加载态三流派（骨架/文字/无） |
| `PageShell.tsx` | `PageShell`（maxWidth+padding）、`Divider` | 页面外壳 2 份 + 手写分隔线 |

### 4. Phase C — 导航单一数据源与全局 token 化

**新建 `components/layout/nav.ts`** — 8 条路由的 path/icon/label 单一定义，`navTitleFor(pathname)` 工具函数。

**Sidebar.tsx** — `NAV` 数组改为 `import { NAV_ITEMS } from './nav'`；`srOnly` 对象删了改用 `className="sr-only"`；遮罩 `rgba(0,0,0,0.5)` → `var(--scrim)`；删不再需要的 8 个 lucide icon import。

**TopBar.tsx** — `TITLES` 对象改为 `import { navTitleFor } from './nav'` + `const title = navTitleFor(pathname)`，修复 /pyramid /audit /llm-log 三条路由标题错误回退到"时间轴"的 bug。

**AppShell.tsx** — 删除未被使用的 `AppShell` 空壳导出，保留实际承载顶栏、侧栏与内容区的 `MainLayout`。

**`#fff` → `var(--accent-contrast)`** 清理本轮涉及组件中的硬编码白色（TopBar、AuthCard、TokenManager、AppOverridesSection、TokenCreatedDialog、TurnView、AgentSidebar、SearchPage、AgentPage、PyramidPage、FeedbackControls、DashboardPage）。

**`rgba(0,0,0,0.5)` → `var(--scrim)`**：Sidebar:134、RecordDetailPanel:54、AgentPage:315、TokenCreatedDialog:52。

### 5. Phase D — 逐页整改

**PyramidPage（问题最多）：**
- 删 `CAT_COLORS` 硬编码 hex 表（L21-29），改用 `lib/categories.ts` 的 `categoryColor()` — 暗/亮双主题自动适配
- 补 `q.isError` 错误态 + `SkeletonRows` 骨架加载态
- 接 `useIsMobile`：移动端详情面板改 Radix Dialog 底部 sheet（复用 RecordDetailPanel 的 mobile 模式）
- sticky 表头纯色补丁 → `color-mix(in srgb, var(--bg-surface) 85%, transparent) + backdrop-blur`（参考 TopBar 写法）

**LlmLogPage：**
- 硬编码 caller 色（`#3b82f6/#a855f7/#10b981/#f59e0b`）→ `var(--cat-blue)/var(--cat-purple)/var(--cat-green)/var(--cat-amber)`
- 加载态换 `SkeletonRows`；错误横幅补重试按钮

**TimelinePage：**
- `useRecords` 的 `isLoading`/`isError` 不再丢弃，加载/失败与"空的一天"三态可区分
- 手写分隔线和按钮换 `Divider`/`Button`

**SearchPage：** 搜索结果加载中换 `SkeletonRows`；主按钮换 `Button variant="primary"`；搜索框 placeholder 缩短；补 `sr-only` label。

**DashboardPage：** 报告区骨架化（标题条 + stat 卡 + 卡片网格骨架）；区分 streaming / 断线重连轮询文案（"生成中…" / "连接中断，正在取回…"）。

**AuditPage：** `HEADERS` 改成 `{label, align}` 对象数组，删 `h === '上传延迟'` 字符串匹配对齐 hack；AuditRow `chips.slice(0,2)` 补 "+N" 提示；换 `LiveBadge`/`PageShell`/`ErrorBanner`。

**AgentPage + AgentSidebar：** 修空对话无法删除（AgentSidebar.tsx:157 移除 `questions.length > 0` 门控）；会话列表补 `EmptyState`；输入框与发送按钮等高 + disabled 态；`window.confirm` 换内联二次确认。

**Settings + admin/*：** 全部迁入 `Card`/`Section`/`Button`/`Chip`；AppOverridesSection 移动端纵向堆叠 + 未保存 dirty 指示（`isDirty` 状态 + unsaved changes warning）。

**Login/ChangePassword：** 错误文本加 `role="alert"`；确认密码校验仅在 confirm 非空时触发（修复过早报错）；删 `outline:'none'`（AuthCard:110, TokenManager:69, AppOverridesSection:36, EmbeddingDiagnostics:186 — 恢复全局 `:focus-visible` 焦点环）。

**TokenCreatedDialog：** 遮罩点击不可关闭（一次性 secret 防丢，`onPointerDownOutside`/`onEscapeKeyDown` 拦截）；补 `tt-overlay-content-in` 进场动画。

**现有组件内部升级（不破 API）：**
- `CategoryBadge` / `StatusChip` — 内部渲染 `<Chip dot>`，对外 props 不变
- `ThemeToggle` — 换 `IconButton`

### 6. Phase E — 路由级代码分割

`App.tsx` 保留首屏 Timeline 静态导入，其余 7 个页面改为 `React.lazy()`，并用 `<Suspense fallback={...}>` 提供骨架占位：

```tsx
const AgentPage = React.lazy(() => import('./pages/AgentPage'))
// ... 其余 6 个非首屏页面
```

Timeline 作为首页留在主 bundle；Dashboard 的 react-markdown 链、Pyramid、Agent 等页面按需加载。`npm run build` 验证 chunk 拆分生效。

### 7. 验证

- `npm run lint` — ✅ 通过，无新增错误
- `npm run build` — ✅ 通过，TypeScript 编译 + Vite 构建均无错误

---

## 遇到的问题与解决

### 问题1：`composes: tt-btn-ghost` 是 CSS Modules 语法、纯 CSS 不支持

在 `.tt-btn-danger` 类中误用了 `composes: tt-btn-ghost;`，编译期发现修复为独立完整声明（复用 ghost 的布局参数）。

### 问题2：`fontFamily: 'JetBrains Mono, monospace'` 硬编码且 `var(--font-mono)` 变量不存在

多文件引用了 `var(--font-mono, ...)` fallback 但 `--font-mono` 从未定义在 `:root`。在 `index.css` 的 `:root` 块新增 `--font-mono` token，`.font-mono` class 改为读变量，收敛 AuditRow/Markdown/Settings/TokenCreatedDialog 四套等宽栈为一致。

### 问题3：Tailwind 4 的 `hidden sm:inline` 类仍然可用

TopBar.tsx:160 用到 `className="hidden sm:inline"` — Tailwind 4 的 `@import "tailwindcss"` 虽然主库几乎没用，但 utility class 仍然有效（由 Vite 插件按需生成）。这些类保留不动，不在本次范围。

---

## 知识清单

- **inline style 的天然限制是 `:hover`/`:focus-visible`/`:disabled` 不可表达** — React 的 `style` prop 不走 CSS 选择器，所以如果全站 inline style，交互态只能靠 CSS class 补。本项目选择 "inline style + 少量 `.tt-*` component class" 的中间路线：布局/间距/动态值继续 inline，交互三态集中在 class 里。
- **`color-mix(in srgb, var(--xxx) N%, transparent)` 是主题安全的软背景色方案** — 比硬编码 hex 或 `opacity` 更"对"（opacity 会影响文字），Chip/CategoryBadge/StatusChip 全用这个模式，深浅主题下都看起来"对"而不用写 per-theme 颜色。
- **`DesignSync` 不是这个项目的工具** — `frontend/` 没有上传到 claude.ai/design 的需求，只写本地文件。
- **`BackdropFilter blur` 只能在非 `body` 元素上生效，是"sticky 表头不变成纯色补丁"的正确方案** — TopBar 用了 `color-mix + backdrop-blur` 磨砂，PyramidPage sticky 表头学了它。
- **`React.lazy` + `Suspense` 路由级代码分割** — 8 页拆分到独立 chunk，Vite 自动 code-split；只要 fallback 用 PageShell+Skeleton 就是无感体验（首屏 Timeline 最快、其余页按需拉）。

---

## 待办 / 遗留

- [ ] `npm run dev` 人工过一遍 8 个页面 × dark/light 双主题（按钮 hover/focus、焦点环恢复、TopBar 标题正确、Pyramid 移动端、各页加载/错误/空态）
- [ ] 登录 → 改密 → 主流程冒烟（auth 链路有 `RequireAuth` 保护，AuthCard 按钮改动需重点验证）
- [ ] DashboardPage 的遗留 HTML 报告模式（`dangerouslySetInnerHTML`，注释标明无主题适配）可以后续清理
- [ ] `useCanvasRenderer.ts` 的 `TIMELINE_PALETTES` 仍与 `index.css` 的 `--timeline-*` 逐字重复（注释说明了 getComputedStyle 竞态原因，本次只加互相指向的注释）
- [ ] `AuditRow.tsx:129` 展开区 `padding:'4px 12px 14px 44px'` 的 44px 是手算 magic number、AUDIT_COLS 改了会错位
