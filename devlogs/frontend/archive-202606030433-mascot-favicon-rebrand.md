# 吉祥物 / favicon / logo 全面换成猫娘 + badge 调色 + 部署上线

**日期：** 2026-06-03
**目标：** 把网站手绘/AI 风格的吉祥物、浏览器 tab favicon、左上角 logo 全部换成新的猫娘描摹图，并部署到公网 VPS；最后把 logo badge 底色从深紫调成浅粉紫→浅蓝。

---

## 背景

- 之前网站的吉祥物（[CatMascot.tsx](../../frontend/src/components/brand/CatMascot.tsx)）是内联手绘紫色猫；favicon（`frontend/public/favicon.svg`）和左上角 logo（[Logo.tsx](../../frontend/src/components/brand/Logo.tsx) 里的 `lucide-react <Cat>` 线条猫）也都是简单图形，用户觉得「AI 生成的、很丑陋」。
- 用户在本机 `D:\Code\20260603猫转svg\gpt\` 用「图转 SVG」管线产出了一张新猫娘图 `final_250kb_ahoge_transparent.svg`（242KB、2048×2048、描摹版、透明底、带呆毛 ahoge），想替换掉旧的。
- 部署拓扑（本会话核实，见下）：**静态前端发在公网 VPS `xcy`（nginx docroot `/var/www/timetrace.yukirin.me`）**，只有 `/v1 /thumbs /blob` 反代到小主机后端（frp 隧道 `127.0.0.1:18765`）。前端**没有 CI 工作流**，`deploy.yml` 只管后端上小主机；前端上线是手动 build → tar → scp 到 VPS → 原子换入。

---

## 操作步骤

### 1. 替换吉祥物 SVG（commit 77cc16d）

- 复制 SVG 进项目：`cp .../final_250kb_ahoge_transparent.svg frontend/src/assets/cat-mascot.svg`（242296 B）。
- 改写 [CatMascot.tsx](../../frontend/src/components/brand/CatMascot.tsx)：从内联手绘 path 改成 `<img src={catMascotUrl}>`，**保留原组件 API（`size`/`float`/`style`）**，8 处调用点零改动。
  - 用 `<img>` 而非内联：这个描摹 SVG 里带一个**固定 id 的 clipPath（`ahoge-alpha-fix`）**，内联多实例会 id 撞车，`<img>` 天然隔离。
- 验证：`tsc -b` 通过（`tsconfig.app.json` 里 `"types": ["vite/client"]` 已声明 `*.svg` 模块），`npm run build` 通过；本地 `npm run dev`（http://127.0.0.1:5173/）起来看效果。
- 提交（精确路径）：`git add frontend/src/assets/cat-mascot.svg frontend/src/components/brand/CatMascot.tsx` → commit `77cc16d` → 守护式 push（`422320d..77cc16d`，FF）。
- 部署 VPS（见步骤 4 的手法）：live bundle `index-BSNwZd-U.js` → **`index-mpZFWRsv.js`**，`/assets/cat-mascot-Dbv52DR4.svg` 线上 200、`image/svg+xml`、242296 B。

### 2. 澄清本地登录 403（非 bug，未改代码）

用户本地登录报 `API /v1/auth/login failed: 403 cross-origin request refused`。排查结论：
- 该 403 来自 [api/app.py](../../src/timetrace/server/api/app.py) 的 `csrf_origin_guard`，**只在 `cookie_secure=True` 时挂载**，且只在「带 session cookie 的 mutating 请求 + Origin host ≠ Host」时触发。
- 本地 dev 经 vite proxy（`changeOrigin:true` 改 Host 到 `127.0.0.1:8765`，浏览器 Origin 仍 `:5173`），加上浏览器里残留旧 `tt_session` cookie → 命中 guard。属本地 dev 现象，与猫图无关。
- 附带答疑：默认登录是文档化的 `admin` / `admin`（首登强制改密；公网 HTTPS 模式无显式种子密码时改为随机生成 `tt-init-...`）——这是设计默认值，非泄密。

### 3. 核实「前端到底发在 VPS 还是小主机」（用户质疑 → 撞真机数据）

用户怀疑「我之前不是改过吗，前端是不是挪到小主机了？」。**不靠记忆，查 ground truth + 验线上：**
- 仓库证据三连：① 后端 `app.py`/`main.py`/`bootstrap.py` 只 mount `/mcp`，无 `StaticFiles` 发前端；② `deploy/deploy.sh` 无 npm/vite/dist/var-www 字样；③ committed nginx `root /var/www/timetrace.yukirin.me` + `location / { try_files … /index.html }`。
- **线上响应头（决定性）**：
  ```
  /assets/index-*.js → Cache-Control: public, max-age=31536000, immutable   ← 只有 VPS nginx 的 location /assets/ 会加
  /healthz           → 405 + application/json                                ← 这才是反代到小主机后端
  ```
- 结论：**静态前端确实发在 VPS docroot**；用户记忆里「改过」指的是**后端** `/v1/` 反代目标从 `8765` 改成 frp 的 `18765`（API 搬小主机），不是前端。

### 4. 前端部署到 VPS 的手法（本会话沿用早先验证过的路径）

```bash
tar -czf ttfe.tgz -C frontend-dist .
# size-verified scp + 重试（最多 8 次）
scp ... ttfe.tgz xcy:/tmp/ttfe.tgz   # 比对 wc -c 两端字节一致
# 轮询线上 bundle hash == 本地 TARGET，每轮做一次 staging+原子换入
ssh xcy 'bash -s' <<EOF
WR=/var/www/timetrace.yukirin.me; STAGE=...
mkdir "$STAGE"; tar -xzf /tmp/ttfe.tgz -C "$STAGE"
test -f "$STAGE/index.html" && ls "$STAGE"/assets/*.js   # 上线前校验
mv "$WR" "${WR}.bak-$TS"; mv "$STAGE" "$WR" || 回滚      # 同盘 mv = 原子换入
EOF
```
关键点：**同盘 `mv` 原子换入 + 时间戳 `.bak-*` 备份可回滚**；轮询线上 `assets/index-*.js` hash 直到等于本地新 build 的 hash 才算成功。

### 5. favicon + 左上角 logo 换猫（commit 3d2c0f6）

- 第一次手动 scp+ssh 部署**被 auto-mode 分类器拦下**（理由：手搓 scp 覆盖生产 webroot、绕过工作流、无显式授权）。如实告知用户「前端无 CI、只能手动传 VPS」，用户明确授权后继续。
- 用 `cairosvg`(2.9.0) + `Pillow`(12.0.0) 栅格化 `cat-mascot.svg`（机器无 ImageMagick，`convert` 是 Windows 自带磁盘工具）：
  ```python
  png = cairosvg.svg2png(url=SRC, output_width=1024, output_height=1024)  # 透明底，处理 clipPath
  im = Image.open(io.BytesIO(png)).convert("RGBA")
  b = im.getbbox(); crop = im.crop(b)        # 内容裁剪
  # pad 成正方形 → LANCZOS 缩放到各尺寸
  master.save("favicon.ico", sizes=[(16,16),(32,32),(48,48)])  # 多分辨率 ico
  ```
- **渲染出来先 Read 看图**：发现新图是动漫 chibi —— 银发猫娘趴在笔记本前（猫耳+呆毛+黄眼+紫蝴蝶结+「?」气泡），是完整**场景**不是单只猫。
- 两个设计岔路问用户（AskUserQuestion，不替他猜）：
  - 裁剪：头部裁剪 vs 完整场景 vs 混合 → 用户选 **完整场景**（明知 16-32px tab 会糊，他接受）。
  - logo 容器：裸猫透明 vs 保留紫色方块 → 用户选 **保留紫色渐变方块**。
- 产出资产：`frontend/public/{favicon-16,favicon-32,favicon-48,apple-touch-icon(180)}.png` + `favicon.ico`(多分辨率) + `frontend/src/assets/cat-mascot.png`(256，给 logo 用)。
- 接线：[index.html](../../frontend/index.html) 删 `favicon.svg` 链、改指 ico/png/apple-touch；[Logo.tsx](../../frontend/src/components/brand/Logo.tsx) 删 lucide `<Cat>`、改 `<img>` inset 到 badge 的 82%（留紫边）+ 容器 `overflow:hidden`；`git rm frontend/public/favicon.svg`。
- 提交 `3d2c0f6`（9 文件精确）→ push（`77cc16d..3d2c0f6`）→ 部署（`index-mpZFWRsv.js` → **`index-BvbzNEEe.js`**）。验证：favicon.ico/-32/apple-touch 线上 200。

### 6. logo badge 底色调浅成粉紫→浅蓝（commit 8bb40c3）

- 用户要把左上角 badge 紫底调浅，「接近粉色的紫 / 浅粉 / 浅蓝」。
- `--grad-brand` 是共享 token（logo badge + 侧栏 AI badge + wordmark 文字渐变），**只改 Logo 这一处 inline background，不动全局**。
- 用 Pillow 合成 3 个浅色候选 + 原色对照的 badge 预览图（带圆角渐变 + inset 猫），Read 出来看，再 AskUserQuestion 让用户挑 → 用户选 **A：`#e6c9ff → #bcd4ff`（粉紫→浅蓝）**，并说「颜色非常完美」。
- [Logo.tsx](../../frontend/src/components/brand/Logo.tsx) 改 `background: 'linear-gradient(135deg, #e6c9ff 0%, #bcd4ff 100%)'` + 注释说明为何不动全局 token。
- 提交 `8bb40c3`（仅 Logo.tsx）→ push（叠在并发后端 commit `c2d94d8` 之上，FF `c2d94d8..8bb40c3`）→ 部署（`index-BvbzNEEe.js` → **`index-B5qIKcMI.js`**）。

---

## 遇到的问题与解决

### 问题1：删了 favicon.svg，线上 `/favicon.svg` 仍返回 200
- **现象：** `curl -I https://timetrace.yukirin.me/favicon.svg` → 200。
- **原因：** nginx `location / { try_files $uri $uri/ /index.html }` —— 文件不存在就**回落到 index.html**（SPA catch-all），所以 200。
- **解决：** 验 `Content-Type` —— 返回 `text/html`（不是 `image/svg+xml`），body 是新 index.html。证明旧文件确实没了，200 只是兜底。

### 问题2：首次 VPS 部署被 auto-mode 分类器拒绝
- **现象：** 手搓 `scp + ssh` 部署被拦：「bypasses the workflow guardrail … no explicit authorization for this manual production deploy」。
- **原因：** 生产 webroot 覆盖属高危外向操作，且用户当时还在问「是不是跑工作流」。
- **解决：** 停手，如实说明「前端无 CI、只能手动传 VPS」+ 部署手法 + 可回滚，拿到用户明确「上」之后再执行。

### 问题3：native Python 在 Windows 写不了 `/tmp`
- **现象：** `Image.save('/tmp/...')` → `FileNotFoundError`。
- **原因：** Git Bash 的 `/tmp` 对 Windows 原生 python 不是真路径。
- **解决：** 用 `tempfile.gettempdir()` 取 Windows 临时目录。

### 问题4：共享工作树并发提交
- **现象：** push 时 origin 已从 `3d2c0f6` 移到 `c2d94d8`（另一个 agent 的后端 MCP 修复）。
- **解决：** 守护式 push（`git merge-base --is-ancestor origin/<ref> HEAD` 判 FF），我的提交只 `git add` 自己那一个文件，干净叠在对方 commit 上 FF 推送；并 `git diff --stat` 确认 `c2d94d8` 是纯后端（不影响 frontend-dist）才部署。

### 问题5（接受的取舍）：完整场景 favicon 在 16-32px 偏糊
- 渲染的 32px 预览证实会糊。已明确告知用户这是「选完整场景」的代价，用户接受。备选方案留待办。

---

## 知识清单

- **SVG→PNG 栅格化（本机可用）**：`cairosvg`(2.9.0) + `Pillow`(12.0.0)；cairosvg 正确处理 clipPath；本机**无 ImageMagick**（`convert` 是 Windows 磁盘工具）。
  - 内容裁剪：`im.getbbox()` 取非透明 bbox → crop → pad 成正方形居中。
  - 多分辨率 ico：`img.save("favicon.ico", sizes=[(16,16),(32,32),(48,48)])`。
  - 小图标质量：高分辨率渲染再 `Image.LANCZOS` 缩放。
- **Vite 资产**：`src/assets/*.svg|*.png` import 默认得到 URL 字符串（类型靠 `tsconfig` 的 `"types":["vite/client"]`）；`public/*` 原样发到 docroot 根；改 favicon 要同时删旧 SVG 的 `<link>` + 文件，否则支持 SVG icon 的浏览器会优先用它。
- **固定 id 的 SVG 用 `<img>`**：描摹 SVG 常带固定 id（clipPath/gradient），内联多实例 id 撞车，`<img>` 隔离更安全。
- **部署拓扑硬事实**：
  - VPS = `xcy`（`103.117.123.204:22000`），nginx 从 docroot `/var/www/timetrace.yukirin.me` 发 SPA。
  - 小主机 = `GTi13-Ultra`（LAN `192.168.2.105:22`）+ frp 路由（`-2v4G` = `121.43.33.13:10089`、`-JPVPS` = `151.242.164.179:6792`）。
  - nginx 反代 `/v1 /thumbs /blob /healthz` → `127.0.0.1:18765`（frp → 小主机后端）；前端无 CI，`deploy.yml`/`deploy.sh` 只管后端。
- **判断静态由谁发（不登机）**：拉 `/assets/*.js` 响应头，有 `Cache-Control: …immutable` 就是 VPS nginx 的 `location /assets/` 在发（后端 uvicorn 不会加这条）。
- **favicon 浏览器缓存极顽固**：换了要硬刷 `Ctrl+Shift+R`、或关 tab 重开 / 无痕窗口。
- **共享 token 的局部覆盖**：要只改一处颜色又不波及全局，就在该组件 inline 写死、别动 `:root` 的 CSS 变量，并留注释说明。

---

## 待办 / 遗留

- [ ] 若觉得 16-32px tab favicon 偏糊 → 把 favicon 单独换成**头部裁剪**版（脸+猫耳+呆毛，小尺寸清晰），logo 仍保留完整场景，两边互不影响。
- [ ] 白猫在**浅色主题**白底场景的可见性（CatMascot 用在侧栏底/空状态/登录卡），如发虚再加底色或描边。
- [ ] （非本会话范畴，来自上一篇归档）scheduler 全 scope 修复 `59d5d34` 仍卡在 frp CI-SSH 隧道、待补部署。

> 敏感信息核查：本会话内容不含 token/key/密码明文（登录 `admin/admin` 是文档化默认种子值，非泄密）；scp/ssh 用的是自有主机别名与 IP，非凭据，无需脱敏。
