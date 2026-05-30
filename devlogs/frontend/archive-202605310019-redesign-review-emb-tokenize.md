# 前端美化重构评审 + embedding 面板令牌化 + 分工交接

**日期：** 2026-05-31
**目标：** 评审 infra agent 完成的前端美化重构（用户布置的审美设计任务），修主 agent 自己份内的问题（embedding 自检面板令牌化），并把剩余项 + 两个新任务整理成 prompt 交接给 infra agent。

---

## 背景

infra agent 完成了一轮前端整体重做（设计令牌系统 + 深浅双主题 + 渐变/阴影/自绘猫吉祥物 + 各页美化 + 对抗审计），9 个提交全部 push 到 `feature/refactor-split`。用户布置的是"一个比较有审美的设计任务"，要主 agent 评审改得怎么样、是否需要补充。分工：主 agent 只管自己的 embedding 那条线，infra agent 管前端美化 + infra。

---

## 操作步骤

### 1. 多维度评审 workflow（6 维并行 + 综合）

用 Workflow 起 6 个并行 reviewer，各带 schema 产结构化 findings，再一个 synthesize agent 去重排序产中文报告：
- **aesthetics**（审美/一致性）、**theme-leaks**（硬编码色不随主题切换）、**a11y**（可访问性）、**regressions**（样式改动是否弄坏功能）、**code-quality**（令牌一致性/死代码/baseUrl 删除是否正确）、**emb-panel-fit**（embedding 面板是否契合新设计语言）。
- 第一次跑：6 维 review 全跑完，但最后 synthesize agent **撞 session 限额**，返回 "You've hit your session limit"，报告没合成出来。
- 限额恢复后**重跑**：7 agent / 86 万 token / 607 次工具调用，出完整报告。

### 2. 评审结论

**总评：成色相当高、真心打磨过、零功能回归**（路由/搜索/时间轴选中/反馈 POST/登录登出改密全端到端核对完好，`tsc -b` 退出码 0）。

**亮点（infra agent 干得好）**：双主题是独立调出的两种气质非机械反色；猫吉祥物自绘内联 SVG + 主题感知 + `aria-hidden` + reduced-motion 停用；品牌渐变有纪律复用；画布无障碍降级正确（canvas `role=img` + 视觉隐藏但可 Tab 的镜像 `<button>` 列表）；ThemeContext 教科书写法；删 baseUrl 改 `@/*` 别名正确。

**🔴 必改 high —— 全部 4 条都指向主 agent 自己的 `EmbeddingDiagnostics.tsx`**（讽刺：它写于令牌系统之前、用硬编码色，infra 的代码反而干净）：cosColor 三档硬编码状态色暗色主题不跟随；PASS/FAIL 徽章硬编码；主按钮 `#2563eb`（bootstrap 蓝）在全站紫渐变旁扎眼；缺 `aria-live` + API Key input 无可访问名称。

**🟡 medium / low（infra 域）**：令牌缺 spacing/type scale；SettingsPage 是"没收尾"那一屏（裸 h3 + ASCII `加载中...`）；colorMap 时间轴色只有暗色版浅色画布发白；若干 `aria-pressed/aria-current/aria-expanded` 缺失；DatePicker 不可键盘导航；TokenCreatedDialog 缺焦点陷阱。两条非阻断旁注：后端 `app.py:37-38` 重复 include records.router；`AppShell.tsx` 疑似零引用导出。

### 3. 樱花"看不见"的真相（用户反馈"网站上没看见樱花"）

只读核对 `index.css`：樱花粉 `rgba(244,114,182)` **只在浅色主题的 `--app-glow`（第 151 行）**，而**默认主题是深色**（`:root` `color-scheme: dark`，深色版 app-glow 只有靛蓝+紫、无粉）。且即便切浅色，它也只是 body 角落一抹 **10% 透明度径向光晕**，几乎不可见 —— 跟"樱花"预期差很远。评审把它夸成"樱花暖意"有点过誉。→ 写进给 infra 的修复任务。

### 4. 主 agent 改自己份内的 emb 面板（high 全清）

外科手术式字面量替换，**不重构组件、不动已令牌化部分**。先只读核对 `index.css` 令牌真实存在（`--success/--warning/--error/--accent-contrast/--grad-accent/--radius-lg/md/pill` 全在），再逐处 Edit：
- `cosColor` 三档 `#16a34a/#ca8a04/#dc2626` → `var(--success/--warning/--error)`
- PASS/FAIL 徽章 + errMsg + primaryButton 硬编码色 → 对应 token
- primaryButton `#2563eb` → `var(--grad-accent)`（与 TokenManager 主按钮一致）；on-accent `#fff` → `var(--accent-contrast)`
- 圆角字面量 `8/6/999` → `var(--radius-lg/md/pill)`
- a11y：结果+stage+error 整块包 `role="status" aria-live="polite"`；API Key input 加 `aria-label`

验证：Grep 确认 **0 残留硬编码色 / 0 裸圆角数字**，13 处 var token 用上；`tsc -b && vite build` 退出码 0。删掉 infra agent 误留的 `frontend/.build_out.txt`（一次 build 日志重定向产物，未被 .gitignore 覆盖、不该进仓库）。commit `2cdf3c1` + push（fast-forward 检查通过）。

### 5. 分工交接

给 infra agent 写完整 prompt，含：① **新任务 A（高优先）移动端布局适配**（现在手机根本没法用：固定侧栏/maxWidth 容器/宽画布在窄屏溢出，要响应式改 AppShell/Sidebar/TopBar/Timeline/Detail/Search/Settings + 触摸目标 ≥44px）；② **新任务 B 樱花修复**（见上根因，让樱花真正可感知，克制不廉价）；③ 评审 medium/low 逐条；④ 两条非阻断旁注；⑤ 收尾约定（每 chunk build 绿、只动自己文件别碰 emb 那条线、push 前 fetch+FF 检查、**别用 `@'...'@` 提交**）。

---

## 知识清单

- **评审/审计用 Workflow 多维并行 + 综合**：每维独立 schema findings，最后一个 agent 去重排序。注意综合 agent 可能撞 session 限额导致前面白跑 —— 可考虑把 6 维结果先落盘再单独合成。
- **canvas 画布吃不了 CSS 变量**：renderer 必须手持一份按 theme 选择的 TS 颜色镜像（`TIMELINE_PALETTES`），主题翻转时 effect 重跑。colorMap.ts 的 app 配色同理 —— 单暗色版会在浅色画布上发白。
- **令牌化前写的组件是技术债重灾区**：EmbeddingDiagnostics 早于令牌系统，4 个评审维度反复点名它。补救是字面量→`var()` 映射，不重构。
- **改前先只读核对令牌真实存在**（不盲信评审给的行号/token 名）：grep `index.css` 确认 `--success` 等都在再动手。
- **樱花教训**：默认深色主题 → 只在浅色定义的氛围色用户永远看不到；且 10% 透明度径向光晕 ≠ 用户预期的"樱花"。氛围装饰要在默认主题下可感知。

---

## 待办 / 遗留

- infra agent prompt 已交付（移动端适配 + 樱花 + medium/low + 旁注）。等 infra agent 执行。
- 主 agent 份内（emb 面板 high）已全清并 push `2cdf3c1`。
- 非阻断旁注待某次清理：后端 `app.py:37-38` 重复 include；`AppShell.tsx` 疑似零引用导出（需全量搜索确认再删）。
- 用户可 `cd frontend && npm run dev` 右上角日/月按钮切深浅色看效果。
