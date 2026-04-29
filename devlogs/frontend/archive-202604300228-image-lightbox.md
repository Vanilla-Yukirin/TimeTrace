# 前端图片放大查看 Lightbox 实装

**日期：** 2026-04-30
**目标：** 为时间线详情面板与搜索结果中的截图缩略图，加上点击放大、上下条切换、底部信息条、动画过渡的 Lightbox 弹层组件。

---

## 背景

TimeTrace 前端已有两处展示截图缩略图的位置：

- `frontend/src/components/detail/ThumbnailView.tsx`：时间线右侧详情面板的缩略图
- `frontend/src/components/search/ResultRow.tsx`：搜索结果行的缩略图（折叠态 80×50、展开态最大 320 高）

但缩略图尺寸太小，画面看不清。需要做一个 Lightbox：

- 点击缩略图 → 居中弹层放大（**非全屏**），背景半透明遮罩 + 模糊
- 关闭：点击遮罩 / 右上角 ✕ / Esc
- 进出与切换均需过渡动画
- 弹层内左右箭头按钮 + 键盘 ← / → 切换"上一条 / 下一条活动"
- 图片底部覆盖一条黑色渐变阴影区，显示分类、时间、应用、URL、VLM 描述

---

## 操作步骤

### 1. Plan 模式探索代码库

并行起两个 Explore subagent，分别调研：

- 图片展示位置、URL 构造方式（`/thumbs/{path}`）、活动数据结构、UI 库
- 弹层组件、键盘事件、Portal、样式系统、依赖清单

**关键发现：**

- `@radix-ui/react-dialog` v1.1.15 已安装但未使用 → 可直接做 Lightbox 骨架
- `lucide-react@1.8.0` 中 `ImageOff`、`X`、`ChevronLeft/Right` 都可用
- 项目用 inline style 为主、Tailwind 为辅，**无 framer-motion**
- 已有 `createPortal` 模式（`TimelineTooltip.tsx`）
- 列表项（`ApiRecord` / `SearchResultItem`）已包含 `vlm_desc`、`category_final`、`app_name`、`window_title`、`url`、`ts_start/end` —— 切换 Lightbox 时无需再请求详情

### 2. 与用户确认两个关键设计点

通过 `AskUserQuestion` 询问：

| 问题 | 用户选择 |
|---|---|
| 切换"上下条"时是否跳过无截图项 | **不跳过**：保留时间相邻关系，无图项显示「该活动无截图」占位 |
| 同一活动的多张截图如何处理 | **只展示首张** `thumb_path`，简化语义 |

### 3. 起草并通过 plan

写入 `~/.claude/plans/fluffy-kindling-pillow.md`，由用户批准后退出 plan mode。

### 4. 实装核心组件

**a. CSS keyframes** — `frontend/src/index.css`

加入 `lightbox-overlay-in/out`、`lightbox-content-in/out`、`lightbox-fade` 五组动画，配合 Radix 的 `data-state="open|closed"` 触发。`lightbox-content-in` 必须保留 `translate(-50%, -50%)`，因为 Radix Dialog.Content 用它做居中：

```css
@keyframes lightbox-content-in {
  from { opacity: 0; transform: translate(-50%, -50%) scale(0.94); }
  to   { opacity: 1; transform: translate(-50%, -50%) scale(1); }
}
.lightbox-content[data-state='open']  { animation: lightbox-content-in  200ms cubic-bezier(0.16, 1, 0.3, 1); }
.lightbox-content[data-state='closed'] { animation: lightbox-content-out 140ms ease-in; }
```

**b. ImageLightbox 组件** — `frontend/src/components/lightbox/ImageLightbox.tsx`（新建）

- Props: `items: LightboxItem[]` + `index: number` + `onIndexChange` + `open` + `onOpenChange`
- 容器 `width: min(1200px, 92vw); maxHeight: 90vh`
- 全局 `keydown` 监听 ←/→（Esc 由 Radix 自动处理）
- 切换图片时给 `<img key={item.id}>` 配合 `lightbox-image-fade` 类做淡入
- 底部信息条：`absolute bottom-0` + `linear-gradient(to top, rgba(0,0,0,0.92) 0%, ..., rgba(0,0,0,0) 100%)`，显示 `CategoryBadge` + 时间区间 + 应用·窗口标题 + URL + VLM 描述
- 无图占位：`<ImageOff size={42} />` + "该活动无截图"

**c. 修改触发点**

- `ThumbnailView.tsx`：把 `<img>` 包成 `<button onClick={onZoom}>`，cursor 改为 `zoom-in`；修复 `onError` 占位逻辑（img 现在在 button 内，`nextElementSibling` 失效，改成从 `parentElement.nextElementSibling` 取占位）
- `RecordDetailPanel.tsx`：透传 `onZoom?: (recordId: string) => void`
- `ResultRow.tsx`：缩略图（折叠态 + 展开态）都做成可点击触发

**d. 父组件状态管理**

- `TimelinePage.tsx`：用 `useRecords` 列表整体映射 `LightboxItem[]`，维护 `lightboxIndex`；切换时同步 `selectedRecordId`
- `SearchPage.tsx`：把搜索结果映射 `LightboxItem[]`，维护索引

### 5. 解决 JSX 结构 bug

第一版把 close 按钮、index 指示器、nav 按钮放错了位置（在 image container `</div>` 之后），导致缺一个 `</div>` 闭合。重构成全部放进 image container 内、与底部信息条同级，问题消失。

### 6. NavButton 定位调整

最初设计 `[side]: -52` 让按钮挂在 Dialog.Content 外侧 52px，但 1024px 视口下会越出屏幕。改为放在 image container 内、`{ left: 14 } / { right: 14 }`，半透明背景叠在图片边缘上。

### 7. 构建验证

```bash
npx tsc --project tsconfig.app.json --noEmit --ignoreDeprecations 6.0
# 仅 3 条 pre-existing unused-import 错误，与 lightbox 无关

npx vite build
# ✓ built in 479ms, 380.47 kB

npx vite dev --port 5187
# HTTP 200
```

确认 `tsc -b` 直接报 baseUrl 弃用是项目预存问题（`git stash` 后同样报错）。

### 8. 用户运行时报错排查

用户启动 `npm run dev` 后看到：

```
[vite] http proxy error: /v1/records?... ECONNREFUSED 127.0.0.1:8765
```

**根因**：后端 `uv run timetrace` 没启。Lightbox 代码无关。让用户在另一终端启动后端即可。

### 9. 处理 review 反馈（P2）

用户提交了一份 review，三条问题：

| 风险 | 文件 | 问题 |
|---|---|---|
| P2 | `ResultRow.tsx` | `<div role="button">` 仍包裹了真实 `<button>`（缩略图、时间轴），按钮嵌套语义混乱 |
| P2 | `SearchPage.tsx` | 提交新搜索时 `lightboxIndex` 未重置，旧索引可能落到新结果的不同活动上 |
| Low | `RecordDetailPanel.tsx` | `<pre>` style 中 `marginTop: 8` 被后置 `margin: 0` 覆盖（pre-existing） |

**修复：**

- ResultRow：去掉外层 div 的 `role="button"` 和 `tabIndex`，把 chevron 提升为真正的 `<button aria-expanded={expanded}>`；外层 div 仅保留 `onClick={toggleExpand}` 给鼠标用户的便利点击；thumb / 时间轴按钮已 `stopPropagation` 不需要改
- SearchPage：新增 `useEffect(() => setLightboxIndex(-1), [submitted])`；`<ImageLightbox>` 的 `index` 加 `Math.min(idx, len - 1)` 钳位，`open` 加 `lightboxItems.length > 0` 防御
- TimelinePage：同样加索引钳位与非空判断
- RecordDetailPanel：`marginTop: 8` + `margin: 0` 合并成 `margin: '8px 0 0 0'`

### 10. 修复 r-c skill 的示例 bug

用户问为什么生成的 commit message 总是用中文顿号 `1、2、3、` 而不是 Markdown 标准 `1. 2. 3.`。

定位到 `~/.claude/skills/r-c/SKILL.md:116-117` 的示例确实写错了。修正后：

```
feat(cache): 增加 Redis 缓存层支持

1. 添加 RedisClient 封装类，支持自动重连和连接池管理
2. 在 UserService 中集成缓存读取逻辑，减少数据库查询
```

并在示例下方补一条说明：列表序号用 `1. `（数字+句点+空格），subject 与 body 之间留一个空行（Conventional Commits 规范要求）。

### 11. Commit 范围与文档同步

`/r-c commit` 检查工作区，发现混着大量后端无关改动（VLM、worker、storage、tests、依赖更新等）。给用户列出**只属于本次 Lightbox 的 7 个文件**（6 改 + 1 新建组件目录），并提醒 `frontend-dist/` 是构建产物不该入仓。

随后用户问是否需要更新 infra 文档。检查 `infra/architecture/web-ui.md`，补充：

- 详情面板职责加上"缩略图可点击放大"
- 核心组件下新增 `ImageLightbox` 一节，记录触发点、上下条切换语义（含**不过滤无图项**的关键决策）、键盘交互、索引钳位/重置等实现要点
- SearchPage 结果展示加一行"行内缩略图触发 Lightbox 切换"

按用户要求作为独立的 `docs(infra)` commit，不并入 `feat(frontend)`。

---

## 遇到的问题与解决

### 问题 1：Radix Dialog.Content 居中与缩放动画冲突

**现象**：`@keyframes lightbox-content-in { from { transform: scale(0.94) } to { transform: scale(1) } }` 会让弹层从左上角缩放，因为覆盖了 Radix 自带的 `translate(-50%, -50%)` 居中变换。

**原因**：CSS `transform` 是单一属性，`scale()` 会重置 `translate()`。

**解决**：keyframes 里完整写出 `translate(-50%, -50%) scale(...)`，保留居中变换。

### 问题 2：ThumbnailView 把 `<img>` 包进 `<button>` 后，错误占位逻辑失效

**现象**：图片加载失败时，`onError` 里 `target.nextElementSibling` 拿到的是 `null`。

**原因**：原代码假设 img 与占位 div 是兄弟节点，但现在 img 被 button 包住了。

**解决**：从 `target.parentElement.nextElementSibling` 取占位，并隐藏整个 button。

### 问题 3：tsc -b 报 baseUrl 弃用直接退出，遮蔽真实错误

**现象**：`npm run build` 在 `tsc -b` 阶段报 `error TS5101: Option 'baseUrl' is deprecated` 后立即退出，看不到自己代码的实际类型错误。

**原因**：`tsconfig.app.json` 配置项目预存问题。

**解决**：用 `npx tsc --project tsconfig.app.json --noEmit --ignoreDeprecations 6.0` 旁路弃用警告，正常完成类型检查。`git stash` 验证此错误确实在 main 上就存在，不属于本次任务。

### 问题 4：搜索结果刷新可能让 lightboxIndex 越界

**现象**：用户打开 Lightbox 后再发起新搜索，`data.items` 变短/变空/重新排序，`lightboxIndex` 可能指向越界或不同的活动。

**解决**：`useEffect(() => setLightboxIndex(-1), [submitted])` 在新搜索提交时关闭弹层；同时给 `<ImageLightbox>` 的 `index` 加 `Math.min(idx, len - 1)` 钳位、`open` 加 `length > 0` 防御。

---

## 知识清单

- **Radix Dialog 自带行为**：portal、focus trap、Esc 关闭、`pointerDownOutside` 关闭、`data-state="open|closed"` 钩子，可直接用 CSS @keyframes + 该属性选择器做动画，无需 framer-motion
- **Lightbox 数据来源决策**：列表项已含 `vlm_desc/category_final/app_name/window_title/url` 等字段，切换时无需 `useRecord(id)` 请求详情，零延迟
- **CSS transform 单属性陷阱**：`scale` 与 `translate` 在 transform 里互相覆盖，keyframes 必须写完整链
- **List+index 模式 vs 自取数据模式**：把列表整体传入子组件 + index，由父组件控制状态，比子组件自取数据更利于测试与边界防御（钳位、重置都集中在父）
- **HTML 按钮嵌套**：role="button" 容器内若包真 `<button>`，浏览器兼容但 a11y 语义混乱；不要伪装成 button，直接 `<div onClick>` 给鼠标 + 显式 `<button>` 给键盘
- **Vite proxy 报错 ECONNREFUSED 127.0.0.1:8765 不是前端 bug**：是后端没启
- **lucide-react v1.8.0** 这个版本号看起来异常但确实存在（pnpm 包），ImageOff / X / ChevronLeft/Right 都可用
- **Tailwind v4 + inline style 混用**：项目以 inline style 为主，Tailwind 类零散使用；新组件按既有风格继续 inline style，仅动画用 CSS 类
- **r-c skill 修正点**：commit body 列表序号必须 `1. `（Markdown 标准），subject 与 body 中间留空行（Conventional Commits）

---

## 待办 / 遗留

- [ ] 工作区还有大量无关改动未提交：VLM 模块（`src/timetrace/vlm/`）、worker / storage / API / tests 等。归本次 Lightbox 提交范围之外，需另起 commit。
- [ ] `frontend-dist/` 是 vite build 产物，**当前未在 .gitignore 中**，建议追加忽略规则后再提交相关分支
- [ ] 未跟踪的 `frontend/src/components/lightbox/ImageLightbox.tsx` 之前 review 时无法纳入 diff；如要做完整 review，需先 `git add -N` 让它进入跟踪
