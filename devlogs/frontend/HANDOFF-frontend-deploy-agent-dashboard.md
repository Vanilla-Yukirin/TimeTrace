# 交接提示词：把新前端（Agent 聊天页 + 看板页）部署到 VPS，并补 nginx 的 SSE 配置

> 这是一份给「另一个 agent」的一次性任务提示词。直接把下面 `===` 之间的内容整段交给它即可。
> 写于 2026-05-31，对应 commit `aa6ab89`（origin/feature/refactor-split）。

---

```
你是 TimeTrace 项目的部署助手。任务：把已经写好并合入 feature/refactor-split 的两个新前端页面
（Agent 聊天页 /agent、近况洞察看板页 /dashboard）构建后部署到公网 VPS，并修一处会影响聊天流式
体验的 nginx 配置。后端已经部署好了（小主机），你只管前端 + nginx。

## 背景（必读，别凭记忆）

- 仓库：Vanilla-Yukirin/TimeTrace，当前活跃分支 feature/refactor-split，最新 commit aa6ab89。
- 部署拓扑（两台机，别混）：
  - 【后端】家里小主机 GTi13-Ultra（NAT 后，FRP 隧道）。已通过 `gh workflow run deploy.yml` 部署，
    跑着 timetrace-server，监听 :8765；FRP 把它暴露到 VPS 的 127.0.0.1:18765。**你不要碰小主机**
    （有"禁手动 ssh 改部署机"的铁律；后端部署只能走 deploy.yml 工作流）。
  - 【前端】公网 VPS，ssh 别名 `xcy`，nginx 托管静态 SPA（域名 timetrace.yukirin.me）+ 反代
    /v1 /thumbs /healthz /mcp 经 FRP 到小主机后端。**前端部署就是手动 scp 到 VPS，这是认可的路径**
    （deploy.yml 完全不管前端；"禁手动 ssh"只针对小主机，不针对 VPS）。
- 前端不被 Python 后端托管。`vite build` 产物输出到仓库根的 `frontend-dist/`。

## 本次要上线的改动（已在 aa6ab89，无需你改代码）

- frontend/src/pages/AgentPage.tsx —— Agent 聊天页（SSE 流式，调 POST /v1/agent/chat）
- frontend/src/pages/DashboardPage.tsx —— 近况洞察看板（GET /v1/reports/latest、POST /v1/reports/generate）
- frontend/src/lib/agentApi.ts —— 这两页的 API 客户端
- 侧栏 Sidebar.tsx + TopBar.tsx + App.tsx 加了「问问」「看板」入口和路由

## 你的步骤

### 1. 同步代码 + 构建（在开发机/本机仓库，不是 VPS）
```bash
cd <仓库根>
git fetch origin feature/refactor-split
git checkout feature/refactor-split && git pull --ff-only
cd frontend
npm ci   # 或 npm install
npx tsc -b          # 必须 exit 0，别只看 vite。tsc 卡未用导入而 vite 不卡
npx vite build      # 产物到 ../frontend-dist/
```
若 tsc 报错先修干净再继续，别带错部署。

### 2. 部署到 VPS（手动 scp，沿用上次前端 agent 的做法）

⚠️ **目标路径有歧义，必须先在 VPS 上核实，别猜**：
- devlog（archive-202605311034）记录上次实际 scp 到 `xcy:/var/www/timetrace.yukirin.me/`
- 但 deploy/nginx-timetrace.yukirin.me.conf 模板写的是 `root /var/www/timetrace`
- **以 VPS 上 nginx 实际生效的 root 为准**。先查：
  ```bash
  ssh xcy "grep -r 'root ' /etc/nginx/sites-enabled/ 2>/dev/null; ls -d /var/www/timetrace*"
  ```
  确认真实的 SPA root 目录（记为 $WEBROOT），后续都用它。

然后备份旧产物 + 替换（保留 index.html 回滚能力）：
```bash
ssh xcy "sudo cp -r $WEBROOT ${WEBROOT}.bak-$(date +%Y%m%d%H%M) && sudo rm -rf $WEBROOT/assets"
scp -r frontend-dist/assets frontend-dist/index.html xcy:/tmp/ttfe/
ssh xcy "sudo cp -r /tmp/ttfe/* $WEBROOT/ && sudo chown -R www-data:www-data $WEBROOT && rm -rf /tmp/ttfe"
```
（如果 VPS 上你的用户对 $WEBROOT 有写权限则不必 sudo；按实际权限调整。）

### 3. 修 nginx：让 /v1/agent/chat 的 SSE 不被 buffer / 不被 60s 超时掐断（重要）

现状（deploy/nginx-timetrace.yukirin.me.conf）：`location /v1/` 只做了基本 proxy_pass，
**没有** `proxy_buffering off`，也没有加长 `proxy_read_timeout`（只有 /mcp 有）。
后果：聊天是 SSE 长流式 + 本地大模型可能要几十秒才出第一字，默认 60s read timeout 会掐断，
buffering 也可能让 token 不逐字出（后端已加 X-Accel-Buffering:no 头能缓解 buffering，但 timeout 仍在）。

在 VPS nginx vhost 里，**在 `location /v1/` 之前**加一个更具体的 location（nginx 最长前缀匹配，
更具体的优先），只对 agent 流式接口放开 buffering + 长超时：
```nginx
    # Agent chat is SSE streaming over a possibly-slow local LLM — must not buffer
    # and must not hit the default 60s read timeout mid-answer.
    location /v1/agent/ {
        proxy_pass http://127.0.0.1:18765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
```
（看板生成 POST /v1/reports/generate 也可能慢，同理；可把上面改成同时覆盖，或给 /v1/reports/ 也加一份。）

应用：
```bash
ssh xcy "sudo nginx -t && sudo systemctl reload nginx"
```
**同时把这段也写回仓库的 deploy/nginx-timetrace.yukirin.me.conf**（让配置进版本控制，按精确路径
只改这一个文件、提交、push；commit message 说明加了 /v1/agent/ 的 SSE location）。

### 4. 验证
```bash
# 新 bundle 上线（assets 文件名变了）
curl -sI https://timetrace.yukirin.me/ | head -1                       # 200
curl -s https://timetrace.yukirin.me/ | grep -o 'assets/index-[^"]*'   # 应是新构建的 hash
curl -sI https://timetrace.yukirin.me/healthz                          # 200（反代到小主机）
```
然后浏览器开 https://timetrace.yukirin.me ，登录后确认侧栏有「问问」「看板」，进「问问」页随便问一句
看是否流式出字，进「看板」页点「重新生成」看是否出 HTML 卡片。

### 5. 前置依赖（确认，不是你做但要知道）
- 聊天和看板都依赖**小主机后端的 .env 里 VLM 配置可用**（小主机 .env 在 FRP 后，部署不碰它）。
  若小主机 VLM 指向本地 LM Studio，需先 `lms load` 加载模型（这是 deploy 流程之外、允许手动的唯一例外）；
  若指向云端（如 SiliconFlow）则一直在线。如果聊天/看板返回"LLM 未配置/调用失败"，多半是这里。

## 纪律（多 agent 共享一个工作树时）
- git 只 `git add <精确路径>`，**绝不 `git add -A`**；提交后 push 前 `git fetch` + 确认 fast-forward。
- commit message 里写的每一步都必须是真做过、真验证过的（别写"已验证"却没跑）。
- 不碰小主机的 git / systemd；VPS 的 nginx + 静态文件可以手动。
```

---

## 附：当前后端部署状态（写这份交接时）

- 后端已部署：`gh workflow run deploy.yml --ref feature/refactor-split` → run 26714765335 **success**，
  小主机已在 aa6ab89，timetrace-server 重启 + healthz 通过。
- 所以 API 层（/v1/agent/chat、/v1/reports/*）已经活在小主机；**只差前端上线 + nginx SSE 配置**，
  公网 demo 即可端到端跑通。
