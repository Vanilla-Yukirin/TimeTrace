# 前端美化重构 + 双主题 + 移动端适配 + 触摸交互 + 两轮对抗审计

**日期：** 2026-05-31
**目标：** 把前端从单一深色硬编码、桌面-only、无触摸的状态，重做为有审美的双主题设计系统 + 移动端可用 + 时间轴可触摸操作，并经两轮多 agent 对抗审计加固，最后部署到公网 VPS。

---

## 背景

用户给了一张 AI 生成的参考图（个人效率仪表盘风格：稳定三栏、按分类配色、带封面的活动预览卡、完整顶栏、圆角软卡、猫吉祥物/樱花/渐变氛围、深浅双主题各有气质），要求把现有偏工程后台的前端"打好框、打好底子"，做得更美观、更有审美。明确要求："一步到位、不要停下来、多 commit、多 achieve"。

随会话推进，用户陆续追加：① 樱花根本看不见要修；② 手机上没法用要做移动端适配；③ 设置页浏览器自动填密码 bug；④ 时间轴刻度标签缩小时密密麻麻不减少；⑤ 时间轴无法触摸拖动/缩放；⑥ 去掉底部冗余"今天"按钮。

**关键环境约束（贯穿）：**
- 多 agent 并发共享同一工作树/.git：本会话是前端 agent，另有 embedding agent（EmbeddingDiagnostics.tsx / api/embedding.ts / embserver 后端）和 infra/docs agent（infra/*.md）同时在改。**只能按精确路径 stage 自己的文件，绝不 `git add -A`**。
- 前端**不被 Python 后端托管**；公网架构 = CF DNS(DNS-only) → VPS nginx(托管静态 SPA + 反代 /v1 /thumbs /mcp 经 frp 到家里小主机后端)。

---

## 操作步骤（按时间顺序）

### 0. 打通构建基线（既有 bug，非本次引入）
本机 TS 6.x 下 `tsc -b` 被 `baseUrl` deprecation(TS5101) 中断；去掉后又暴露两处被短路屏蔽的既有错误：`UnauthorizedError` 用了构造器参数属性（`erasableSyntaxOnly` 禁止）、`useCanvasEvents` 未用 `useEffect` import。全修 → `tsc -b && vite build` 绿，作为后续验证基线。

### 1. 设计令牌系统 + 双主题地基（commit 设计令牌）
- `index.css` 重写：深/浅两套 CSS 变量（surface 分层 / 文本 / accent / 渐变 / 阴影 / 圆角 / 语义色 / 分类色 / 时间轴色），各自独立氛围；`--app-glow` 双径向辉光背景；主题切换 0.22s 颜色过渡；`prefers-reduced-motion` 兜底；`.text-gradient/.tt-rise/.tt-float` 工具类
- `index.html`：首屏前内联脚本按 localStorage 设 `data-theme`，杜绝 FOUC；lang=zh-CN
- `contexts/ThemeContext`：读已上屏的 data-theme 做初值（无闪烁）+ 持久化 + 跨 tab 同步
- 复用件：`brand/Logo`（渐变猫徽标）、`brand/CatMascot`（主题感渐变猫 SVG）、`ui/ThemeToggle`、`lib/categories`（分类→主题感配色）

### 2. 应用外壳（TopBar + Sidebar）
TopBar 左侧页标题、右侧搜索/主题切换/渐变头像/退出；半透明毛玻璃。Sidebar 渐变 Logo + 激活态 + 底部猫吉祥物窝。

### 3. 时间轴页
画布渲染主题化（`renderTimeline` 接 `TimelinePalette`，按 theme 选常量而非 getComputedStyle，规避主题切换 effect 时序竞态）；圆角块 + 高光 + 选中辉光；新增 `CategoryFilter`「快速筛选」实时分类计数；日期头（M月d日+星期+今天徽标+统计+回到今天）；DatePicker 美化。

### 4. 详情面板 → 活动预览卡
统一 `Shell`、封面圆角、分类色左强调条、胶囊徽标、分区小标题、空态猫吉祥物。

### 5. 登录/改密页
`components/auth/AuthCard`（全屏氛围 + 角落主题切换 + 悬浮玻璃卡 + 猫吉祥物 + 渐变标题 + AuthField/AuthButton），鉴权逻辑原样保留只换皮。

### 6. 搜索/设置/共享件收尾
搜索框渐变钮 + 空态吉祥物；ResultRow/FilterPanel/ImageDropzone/TokenManager/AccountSection 半径/token 统一。

### 7. 第一轮对抗审计（4 维 21 agent，13 confirmed）→ 全修
对比度（浅色下分类徽章/`--text-muted`/`--warning` 文字不达 AA → 文字改 `--text-primary`、token 上调）、a11y（画布加 role=img + 隐藏可聚焦活动列表满足 WCAG 2.1.1、补 aria-label、TokenCreatedDialog 务实化）、favicon 换品牌猫、FeedbackControls 残留 `borderRadius:4`。

### 8. 部署 + 拓扑澄清
build → `备份 + rm assets + scp frontend-dist/* 到 xcy:/var/www/timetrace.yukirin.me/` → curl 验证。**向用户解释了为何前端在 VPS 而非走 GitHub Actions**：deploy.yml 只部署后端→小主机；前后端两台机；"禁手动 ssh"铁律只针对小主机（deployment mirror），VPS 静态文件不在其内；首次上线本就是手动 scp。用户随后选择拓扑 C（前端挪回小主机走 frp、VPS 纯反代），但因**涉及防火墙、需回家操作**，**暂缓**。

### 9. 樱花真正可见
根因：粉色只在浅色主题 `--app-glow` 且仅角落 10% 光晕，默认深色无粉。修：双主题 glow 加克制樱花粉 + 新增 `SakuraPetals`（7 片稀疏慢速飘落花瓣，z-index:-1 透明区透出，aria-hidden，reduced-motion 整层隐藏）+ `--sakura` token。

### 10. 移动端适配
inline style 写不了 `@media` → 引入 `useIsMobile()` matchMedia 钩子。MainLayout 改为自渲染 TopBar+Sidebar 并持有移动抽屉态；Sidebar 桌面静态列 / 移动滑入抽屉；TimelinePage 窄屏单列 + 折叠日历筛选 + 详情全屏覆盖层；Search/Settings 收紧并修 Settings 滚动容器。

### 11. a11y 状态 + 浅色画布 + 设置页收尾
aria-pressed/expanded/current + aria-hidden 装饰图标；DatePicker 日格补完整日期 aria-label；浅色画布块用更高 alpha（0.9）避免发白 + hover 叠加主题感知（深=提亮、浅=压暗）；SettingsPage 重复 info-card 抽 `InfoRow` + 省略号修正。

### 12. 刻度自适应稀疏 + 防自动填密码
`tickInterval` 改为从 nice 间隔表（1/5/10/15/30min·1/2/3/6/12h）挑「屏上间距 ≥58px 的最小档」，缩小/窄屏自动升档，标签去秒用 `HH:mm`。TokenManager 标签框加 `name`+`autoComplete=off` 打断"文本+密码=登录表单"配对（自动填的主要触发）。

### 13. 第二轮对抗审计（4 维 14 agent，2 confirmed）→ Radix 迁移
仅揪出 2 个真问题（均我新加的移动覆盖层）：手写抽屉 + 详情覆盖层声明 modal 却缺焦点陷阱/回归/背景 inert/Esc/**body 滚动锁**（滚动锁真切影响触摸用户）。按一致建议迁到已装且 ImageLightbox 已用的 `@radix-ui/react-dialog`，全白拿。无功能回归/刻度数学错误/溢出/主题问题。

### 14. 时间轴触摸交互 + 去冗余按钮
canvas 加 touchstart/move/end（`passive:false` + `touch-action:none`）：单指拖动平移、双指捏合缩放（锚点取两指中点、增量 `lastDist/newDist` 复用现有 zoom）、轻点选中。**手势态存 ref**（渲染 effect 频繁重跑不打断手势）、records/viewport 经 ref 取最新做命中。去掉底部"今天"按钮（回到今天已在顶部）+ 移除 `onGoToday` prop。

---

## 遇到的问题与解决

### 问题1：`@` 开头的 commit subject（并发 agent 在踩）
**现象：** embedding/infra agent 反复产出 `@ feat(...)` / `@ docs(...)` 标题。
**根因（本会话定位）：** 在 **Bash/Git Bash** 里写 `git commit -m @'...'@` 会被解析成「`@` + 单引号串 + `@`」，把字面 `@` 塞进标题首行。`@'...'@` 只有在 **PowerShell** 才是 here-string。
**解决：** 我全程用 `git commit -F - <<'EOF' … EOF`（here-doc），标题干净；并把根因转告其它线。

### 问题2：并发 agent 的 `git add -A` 扫走我的改动
**现象：** 我 stage 的文件被并发 session 的提交吞进它的 commit。
**解决：** 一律 `git add <精确路径>` 单文件 stage，提交后立即 push；push 前 `git fetch` + `rev-list --left-right` 确认 fast-forward。

### 问题3：浏览器在设置页自动填账号密码
**现象：** token 标签框被填 `admin`、嵌入 key（type=password）框被填保存的登录密码。
**原因：** 浏览器凭 ①`type=password` ②文本框+密码框**配对**=登录表单 ③关键词 ④autocomplete 判定凭据页。设置页恰好凑成"文本+密码"对。
**解决：** 给非凭据框标 `autoComplete=off` + 非账号名 `name`，打断配对。TokenManager（本人）已改；EmbeddingDiagnostics 的密码框（嵌入 agent 文件、其正在编辑）一行同款修复已转交。

### 问题4：移动覆盖层缺 modal 行为（滚动锁影响触摸）
**解决：** 迁 `@radix-ui/react-dialog`，白拿焦点陷阱/回归/inert/Esc/滚动锁。

### 问题5：触摸手势被渲染 effect 重跑打断
**原因：** 渲染 effect 每次 pan/zoom 重跑；若手势态放 effect 闭包会被重置。
**解决：** 手势态放 `useRef`（跨重渲染持久），触摸 effect 依赖只用稳定的 zoom/pan/onSelectRecord（一次性 attach）。

---

## 知识清单

- **inline-style 无 `@media`**：响应式靠 `useIsMobile()` matchMedia 钩子驱动布局分支。
- **canvas 触摸**：必须 `addEventListener(..., {passive:false})` 才能 `preventDefault`（React 合成 touch/wheel 默认 passive）；配 `touch-action:none` 关浏览器默认手势 + 抑制合成 mouse 事件；手势态务必放 ref。
- **canvas 主题色**：canvas 读不了 CSS 变量，按 `theme` 选 TS 常量副本，避免 getComputedStyle 的 effect 时序竞态。
- **刻度密度**：按"屏上像素间距 ≥阈值"挑最小 nice 间隔，天然适配缩放与窄屏；标签去冗余秒。
- **浏览器自动填密码**：`type=password` + 文本框配对 = 登录表单；非凭据框用 `autocomplete=off`+非账号名 `name` 打断；真登录页才保留 `username`/`current-password`。
- **模态别手写**：focus 陷阱/回归/inert/Esc/滚动锁全是坑，直接用 `@radix-ui/react-dialog`（仓库已装、ImageLightbox 已用）。
- **WCAG AA 对比度**：饱和分类色当正文在白底常 <3:1；色相只放圆点/描边/淡底，文字用 `--text-primary`。
- **commit message**：Git Bash 里禁用 `@'...'@`（PowerShell here-string），改 `git commit -F -` here-doc。
- **多 agent 共享树**：精确路径 stage + 频繁提交 + push 前 ff 检查。

---

## 待办 / 遗留

- [ ] **拓扑迁移（前端挪回小主机走 frp）**：用户已选但**暂缓**（涉防火墙、需回家）。方案：小主机起轻量静态服务（非 FastAPI 托管）监听新端口 → frpc 加第 2 个代理 → VPS nginx `location /` 改 `proxy_pass` → 前端产物改投小主机（理想经 deploy 工作流）。动小主机须走部署流程、不手动 ssh。
- [ ] **嵌入 agent handoff**：① EmbeddingDiagnostics 密码框补 `autoComplete=off`+`name`；② 配置默认值/模式设计——本地零配置（默认 `127.0.0.1:8766`、免手填 key、自动读 embserver token/同源代理）vs 远程才露 URL+key。
- [ ] **长范围分析页（规划）**：用户提出"选某时间段→某时间段做统计分析 + agent"不属时间轴功能，应单开一页（统计学 + agent）。本会话仅规划，未实现。建议：新路由 `/analytics`，输入起止日期范围 → 后端聚合（分类/应用时长占比、趋势）+ 可选 agent 自然语言总结；时间轴页只管单日回放。
- [ ] **可选打磨（非必需）**：全站字号/间距 token 大迁移（纯 churn，已只新增 scale token 不强迁）；TokenCreatedDialog 完整 Radix 化（现为务实版）。
- [ ] 每次部署前端到 VPS 仍是手动 scp（自动模式每次需重新授权）；可补 `deploy-frontend.yml`。

---

## 最终结果

本会话前端共约 15 个提交，全部 build 绿、按精确路径只提交自己的文件、push 前 ff 检查；分批 scp 部署到 `https://timetrace.yukirin.me` 并 curl 验证（新 bundle 200、旧 404、healthz 200）。两轮多 agent 对抗审计共确认 15 项、逐条对抗验证后全修。深/浅双主题、移动端三大页可用、时间轴支持单指拖动+双指缩放+点选、樱花可见、刻度自适应、设置页不再被浏览器塞密码。
