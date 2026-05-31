# 部署 Agent 聊天页 + 看板页到公网 VPS + 补 nginx SSE 透传

**日期：** 2026-06-01
**目标：** 把另一 agent 写好并已合入 `feature/refactor-split`（commit `aa6ab89`）的两个新前端页面（Agent 聊天页 `/agent`、近况洞察看板页 `/dashboard`）构建后部署到公网 VPS，并补一处会掐断聊天流式的 nginx 配置。

---

## 背景

- 这是一次**多 agent 协作**：上游（主/embedding agent）写完前端页 + 后端路由（`/v1/agent/chat` SSE、`/v1/reports/*`）并已部署后端到家里小主机（`gh workflow run deploy.yml` → 小主机跑 `aa6ab89`），然后留下一份交接提示词 `devlogs/frontend/HANDOFF-frontend-deploy-agent-dashboard.md`，把"前端构建上线 + nginx SSE"这块交给本 agent。
- **部署拓扑（两台机，别混）**：
  - 【后端】家里小主机 `GTi13-Ultra`（NAT 后 + FRP 隧道），监听 `:8765`，FRP 暴露到 VPS 的 `127.0.0.1:18765`。**铁律：不碰小主机**（后端只走 `deploy.yml` 工作流，禁手动 ssh）。
  - 【前端】公网 VPS（ssh 别名 `xcy`，域名 `timetrace.yukirin.me`），nginx 托管静态 SPA + 反代 `/v1 /thumbs /healthz /mcp` 经 FRP 到小主机。**前端部署 = 手动 scp 到 VPS，这是认可路径**（`deploy.yml` 完全不管前端，"禁手动 ssh"只针对小主机）。
- 本次要上线的代码（已在 `aa6ab89`，无需改代码）：
  - `frontend/src/pages/AgentPage.tsx` —— Agent 聊天页（SSE 流式，`POST /v1/agent/chat`）
  - `frontend/src/pages/DashboardPage.tsx` —— 近况洞察看板（`GET /v1/reports/latest`、`POST /v1/reports/generate`）
  - `frontend/src/lib/agentApi.ts` —— 这两页的 API 客户端（fetch + ReadableStream 拆 `\n\n` 解析 SSE；`credentials:'include'` cookie 鉴权）

---

## 操作步骤

### 1. 读交接 + 核对 git 状态

读 `HANDOFF-frontend-deploy-agent-dashboard.md`；`git status` / `git log`：当前在 `feature/refactor-split`，HEAD `aa6ab89`，与 origin 同步（0/0），工作树干净（仅 `.claude/scheduled_tasks.lock` 临时锁 + HANDOFF doc 未跟踪）。代码已 push，本 agent 只需构建 + 部署 + nginx。

### 2. 构建前端（本机，非 VPS）

```bash
cd /d/Github/TimeTrace/frontend
npx tsc -b            # 必须 exit 0，别只看 vite —— tsc 卡未用导入而 vite 不卡（交接里特别警告，亲测重要）
npx vite build        # → ../frontend-dist/
```
结果：`TSC_EXIT=0`、`VITE_EXIT=0`，新 bundle `assets/index-DUc91w2Z.js`（CSS `index-oI-KwCYU.css` 未变）。

### 3. 只读勘察 VPS，消除目标路径歧义

交接里标了红：devlog 记 scp 到 `/var/www/timetrace.yukirin.me/`，但 nginx 模板写 `root /var/www/timetrace`。**先查不猜**：

```bash
ssh xcy 'whoami; ls -ld /var/www/timetrace*; ls -la /etc/nginx/sites-enabled/; sudo nginx -t'
```
关键发现：
- VPS 登录用户是 **root**（无需 sudo）。
- webroot 唯一是 `/var/www/timetrace.yukirin.me`（`/var/www/timetrace` **不存在** —— 模板里的 `root` 是对的，歧义是虚惊）。
- `sites-enabled/timetrace.yukirin.me` 是指向 `sites-available/` 的软链。
- 当前线上 bundle `index-Dw7V1pzE.js`（上次部署）。

**确认线上 nginx 配置与仓库模板字节一致**（这样改+scp 仓库文件只会加我的两块、不会覆盖线上独有设置）：
```bash
tr -d '\r' < /d/Github/TimeTrace/deploy/nginx-timetrace.yukirin.me.conf | sha256sum
ssh xcy "tr -d '\r' < /etc/nginx/sites-available/timetrace.yukirin.me | sha256sum"
# 两边都是 46d14f0c...761e9b → 完全一致（CR 剥离后对比，规避 CRLF 噪声）
```

### 4. 改仓库 nginx 配置（加 SSE 透传）

现状：`location /v1/` 只有基础 `proxy_pass`，**没有** `proxy_buffering off` 也没加长 `proxy_read_timeout`（只有 `/mcp/` 有流式三件套）。后果：聊天是 SSE 长流式 + 本地大模型可能要数十秒出第一字，默认 60s read timeout 会掐断；看板 `POST /v1/reports/generate` 同样会阻塞数十秒。

在 `deploy/nginx-timetrace.yukirin.me.conf` 的 `location /v1/` **之后**加两个更具体的 prefix location（复用 `/mcp/` 已验证的参数）：
```nginx
    location /v1/agent/ {
        proxy_pass http://127.0.0.1:18765;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering    off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    location /v1/reports/ { ...同参数... }
```
**nginx location 匹配原理**：本 vhost 无 regex(`~`) location，所以走最长前缀匹配 —— `/v1/agent/`（更长）优先于 `/v1/`，对 `/v1/agent/chat` 生效。`proxy_pass` 不带 URI 部分 → 原始请求 URI 原样透传。

### 5. 部署前 3 路对抗审计（Workflow）

跑了个并行 workflow（`agentType:'Explore'` + structured schema），3 个对抗 lens：
- **nginx-correctness** → `safe-to-deploy`。确认前缀优先级对、`proxy_pass` URI 透传对、后端已发 `X-Accel-Buffering: no`（buffering 即使不设也会关，但 location 级设置是 best practice）、`/mcp/` 是充分先例、不会破坏既有路由。
- **frontend-prod** → `safe-to-deploy`。同源 fetch + `credentials:'include'` cookie 会带、SameSite=Lax+Secure 正确、SSE 拆 `\n\n` + try-catch skip 处理了半包/keepalive、错误非 2xx 不崩。
- **deploy-safety** → **`fix-first`**，3 条 blocking（均采纳）：
  1. `rm assets` 再 `cp` 之间有 **404 窗口**（cp 失败则线上裸 HTML 无 JS/CSS）→ 改用 staging + 原子 `mv`。
  2. `cp` 不完整无校验、无自动回滚 → 部署前先校验上传完整性，保留 backup。
  3. nginx 改写无恢复点 → 覆盖前先备份 + 失败自动恢复 + `systemctl is-active` 复查。

### 6. 部署 SPA（整树原子 rename 交换）

先勘察 webroot 内容（只有 `assets/ favicon.svg icons.svg index.html`，无 `.well-known`；`/var/www` 与 `/tmp` 同在 `/dev/vda1` 同一文件系统、23G 空闲）→ 整树原子交换最干净（零 404 窗口）。

```bash
# 1. 上传到 /tmp 并验证完整
ssh xcy "rm -rf /tmp/ttfe-new && mkdir -p /tmp/ttfe-new"
scp -r /d/Github/TimeTrace/frontend-dist/. xcy:/tmp/ttfe-new/

# 2. 校验 → 同盘 staging → 原子换入（带回滚 guard）
ssh xcy 'bash -s' <<'SH'
set -e
WR=/var/www/timetrace.yukirin.me; SRC=/tmp/ttfe-new; TS=$(date +%Y%m%d-%H%M%S); STAGE="${WR}.staging-$TS"
test -f "$SRC/index.html" || { echo ABORT; exit 1; }
ls "$SRC"/assets/*.js >/dev/null 2>&1 || { echo ABORT; exit 1; }
ls "$SRC"/assets/*.css >/dev/null 2>&1 || { echo ABORT; exit 1; }
rm -rf "$STAGE"; cp -r "$SRC" "$STAGE"; chmod -R a+rX "$STAGE"
mv "$WR" "${WR}.bak-$TS"
mv "$STAGE" "$WR" || { echo "SWAP FAILED"; mv "${WR}.bak-$TS" "$WR"; exit 1; }
rm -rf "$SRC"
SH
```
`mv` 在同一文件系统内是原子 rename（微秒级），旧树留作 `…bak-20260601-005008/` 回滚。

### 7. 部署 nginx 配置（test + reload + is-active + 自动恢复）

```bash
scp /d/Github/TimeTrace/deploy/nginx-timetrace.yukirin.me.conf xcy:/tmp/tt-nginx.conf
ssh xcy 'bash -s' <<'SH'
set -e
SA=/etc/nginx/sites-available/timetrace.yukirin.me; TS=$(date +%Y%m%d-%H%M%S)
cp "$SA" "${SA}.bak-$TS"          # 覆盖前先留恢复点
cp /tmp/tt-nginx.conf "$SA"; rm -f /tmp/tt-nginx.conf
if nginx -t; then
  systemctl reload nginx; sleep 1
  systemctl is-active --quiet nginx && echo "NGINX_OK" || { cp "${SA}.bak-$TS" "$SA"; systemctl restart nginx; exit 1; }
else
  echo "NGINX_TEST_FAILED -- restoring"; cp "${SA}.bak-$TS" "$SA"; exit 1
fi
SH
```
结果：`nginx -t` 通过、reload 后 active、两个新 location 在第 93/105 行。config 备份 `…bak-20260601-005059`。

### 8. 线上端到端验证

```bash
B=https://timetrace.yukirin.me
curl -sS -o /dev/null -w "%{http_code}" "$B/"                                 # 200
curl -sS "$B/" | grep -o 'assets/index-[^"]*'                                 # index-DUc91w2Z.js（新）
curl -sS -o /dev/null -w "%{http_code}" "$B/assets/index-DUc91w2Z.js"         # 200 application/javascript
curl -sS -o /dev/null -w "%{http_code}" "$B/assets/index-Dw7V1pzE.js"         # 404（旧 bundle 已清）
curl -sS "$B/healthz"                                                         # {"status":"ok"} 200
curl -sS -o /dev/null -w "%{http_code}" "$B/v1/reports/latest?scope=recent_24h" # 401（到后端鉴权层，非 nginx 404）
curl -sS -o /dev/null -w "%{http_code}" "$B/v1/agent/chat"                     # 405（后端 POST-only 路由命中）
```
**401/405 是想要的信号**：证明 `/v1/agent/` 与 `/v1/reports/` 两个新前缀都经反代打到了后端（nginx 不会对缺失 location 生成 405）。

### 9. 提交 + 推送（多 agent 纪律）

提交前 `git fetch` 发现**本地 HEAD 已从 `aa6ab89` → `ccfdc83`**（与 origin 同步）—— 另一 agent 在我构建/部署期间在共享工作树里提交并 push 了。查证 `ccfdc83` 改了什么：

```bash
git log --oneline aa6ab89..ccfdc83   # ccfdc83 style: ruff format + 删 unused import，修长期红的 CI format-check
git diff --stat aa6ab89 ccfdc83 -- frontend/src ...   # 空 → 零 frontend 改动
```
`ccfdc83` 纯后端 `ruff format`（`src/timetrace/**` + `tests/**`），**零 `frontend/` 改动** → 我在 `aa6ab89` 构建的 bundle **不是旧的**。安全提交：

```bash
git add deploy/nginx-timetrace.yukirin.me.conf     # 只 stage 精确路径，绝不 git add -A
git commit -F - <<'EOF'
feat(nginx): /v1/agent/ + /v1/reports/ 补 SSE 透传（buffering off + 3600s 超时）
...
EOF
# 守护式 ff push：再 fetch，确认 origin == 我的 parent(ccfdc83) 才 push
git push origin feature/refactor-split    # ccfdc83..e639a61
```

---

## 遇到的问题与解决

### 问题1：目标 webroot 路径有歧义

**现象：** devlog 记 `/var/www/timetrace.yukirin.me/`，nginx 模板写 `root /var/www/timetrace`。
**原因：** 模板注释里的旧路径 vs 实际生效路径未对齐的疑似漂移。
**解决：** 只读 `ls -ld /var/www/timetrace*` + 看线上 config 实际 `root` —— 确认唯一是 `/var/www/timetrace.yukirin.me`，`/var/www/timetrace` 根本不存在。歧义是虚惊，模板是对的。**教训：路径有歧义时先查实际生效值，别猜也别按任一文档照抄。**

### 问题2：部署安全审计判 fix-first（404 窗口 + 无校验回滚）

**现象：** 原计划 `rm -rf $WR/assets && cp -r ...` 有竞态：cp 失败则线上长时间裸 HTML。
**原因：** 非原子操作 + 无上传完整性校验 + 无自动回滚。
**解决：** 采纳审计建议改成 **上传校验 → 同盘 staging → 两次原子 `mv` 交换**（窗口微秒级）+ 全树 backup。nginx 同理：覆盖前先备份恢复点 + 失败自动 `cp` 回滚 + `is-active` 复查。

### 问题3：多 agent 共享工作树，HEAD 在部署期间被推进

**现象：** 提交前 `git fetch` 后发现本地 HEAD 从 `aa6ab89` 变成 `ccfdc83`。
**原因：** 另一 agent 在同一工作树提交了 ruff format 清理并 push（我的未提交 nginx 改动因是工作树改动、未被 HEAD 推进影响，仍完好）。
**解决：** **不盲目提交** —— 先 `git diff --stat aa6ab89 ccfdc83` 查证是否动了 `frontend/`（结论：纯后端、零前端 → 我部署的 bundle 不是旧的），再 `git add <精确路径>` + 守护式 ff push（push 前再 fetch 确认 origin == parent）。

---

## 知识清单

- **nginx location 优先级**：无 regex(`~`) location 时走**最长前缀匹配**；要给某子路径单独加参数，加一个更长的 prefix location 即可压过父级（`/v1/agent/` > `/v1/`）。`proxy_pass http://host:port;`（不带 URI 部分）→ 原始 URI 原样透传。
- **SSE over nginx 三件套**：`proxy_buffering off` + `proxy_read_timeout` + `proxy_send_timeout` 加长（默认 read timeout 60s 会从中间掐断慢流式）。后端发 `X-Accel-Buffering: no` 也能关 buffering，但 timeout 仍需 nginx 侧设。本仓 `/mcp/` 是已验证先例。
- **401/405 当作"路由正确"的证据**：反代后对 POST-only 路由发 GET 得 405、对鉴权路由不带 cookie 得 401，都证明请求打到了后端（nginx 不会对缺失 location 生成 405），比起只看 200 更能区分"nginx 兜底 vs 真到后端"。
- **零停机原子换静态目录**：`/tmp` 与 webroot 若同文件系统，用 `cp` 到同盘 staging 再两次 `mv`（rename 原子）交换，窗口微秒级；旧树 rename 成 `.bak-<ts>` 即回滚点。比 `rm+cp` 安全得多。
- **CRLF 安全比对两端文件**：`tr -d '\r' < f | sha256sum` 两边比，规避 Windows 仓库 vs Linux 线上的换行噪声。
- **本机 Bash = Git Bash**：D 盘路径写 `/d/...`；`commit -F -` 配 `<<'EOF'` here-doc（不是 PowerShell 的 `@'...'@`）。
- **多 agent 共享工作树纪律**：只 `git add <精确路径>`（绝不 `-A`）；commit 前 `git fetch` 看 HEAD 是否被推进、若变了先 `git diff` 查影响；push 前再 fetch 做守护式 ff 检查。
- **VPS 勘察**：`ssh xcy` 登录用户是 **root**（部署写 `/var/www` / `/etc/nginx` 无需 sudo）。

---

## 待办 / 遗留

- [ ] **（用户验证，非本 agent 职责）** 聊天页/看板页能否真出字/出卡片，取决于**小主机后端 `.env` 的 VLM 是否可用**：指向本地 LM Studio 需先 `lms load`（deploy 流程之外、允许手动的唯一例外）；指向云端则一直在线。若报"LLM 未配置/调用失败"多半在此。浏览器登录后试一句即可端到端验证。
- [ ] **（可选housekeeping）** VPS `/var/www/` 下已攒 5+ 个旧 `…bak-*` 目录（含本次新增）；想清理是破坏性操作，本次未动。
- [ ] `devlogs/frontend/HANDOFF-frontend-deploy-agent-dashboard.md` 仍未跟踪（是交接 agent 的一次性提示词，按"只动该动的"未提交，未来想纳入版本控制可单独决定）。
- 已完成：前端新 bundle 上线 + nginx SSE 配置（commit `e639a61` 已 push）。
