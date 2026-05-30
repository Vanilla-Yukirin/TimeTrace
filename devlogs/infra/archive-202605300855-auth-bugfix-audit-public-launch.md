# 登录系统 bug 修复 + 对抗式安全审计 + 公网正式上线

**日期：** 2026-05-30
**目标：** 修用户实测的 2 个前端 auth bug → 多 agent 对抗审计揪出剩余隐患 → 全修后正式开公网（nginx 托管 SPA + frp 隧道 + HTTPS）。

---

## 背景

上一篇（[archive-202605300222](archive-202605300222-phase7-deploy-qwen-restore.md)）部署完登录系统后端。用户在浏览器隧道实测，报了两个 bug + 一句话授权开公网："公网可以开，反正 auth 是做好了。但是鉴权、前后端构建、美化，你自己好好优化一下。多用现成框架，多考虑周到所有情况，推敲是否有 bug。"

用户报的 2 个 bug：
1. 修改密码后点"修改"，不显示成功、不跳转（既不回登录页也不回主页）
2. 主页右上角退出登录，不退出而是闪烁，F5 刷新才回登录页

---

## 操作步骤

### 1. 两个 bug 的根因（同一个）+ 修复（commit 176eb3e）

**根因**：`AuthContext` 里 `user = meQuery.data ?? null`，但 react-query 在后续 fetch **401 出错时保留上一次成功的 data**（stale）。
- 退出：清了 cookie 但缓存 user 还在 → LoginPage 看到 user 真值往主页弹 → 反复横跳（闪烁）；F5 整页重载才清缓存
- 改密：改完没等 `/me` refetch 就 `navigate('/')` → RequireAuth 读到 stale `must_change=true` → 弹回改密页

**修复**（AuthContext 重写）：
- `user = meQuery.isError ? null : (data ?? null)` —— 401 错误态视为登出，不吃 stale
- `refetch(): Promise` —— 可 await，成功刷新清除 error 态；login/change-pw `await refetch()` 再 navigate
- `setUser(me|null)` —— 乐观写缓存；logout 同步 `setUser(null)` 不靠异步 refetch（消除闪烁）
- 美化：加 `sonner` toast（登录/改密/退出/token CRUD 全有反馈，解决"不显示成功"）

**顺带修 .gitignore 大坑**（在上一轮 Phase 5 已修）：Python 模式 `lib/` 把 `frontend/src/lib/` 整个吞了，那层文件从未入库。

### 2. 多 agent 对抗审计 workflow（23 raw → 13 confirmed）

开公网前跑 4 维度并行审计（前端状态机 / 后端安全 / 公网暴露面 / 构建部署）+ 每条 finding 独立对抗验证。揪出 **2 个 HIGH**：

- **HIGH-1：`must_change_password` 后端零强制**。强制改密只是前端跳转；攻击者用默认 admin/admin POST `/v1/auth/login` 拿到有效 cookie，**绕过前端**直接 `GET /v1/records`、下载 `/thumbs`、`POST /v1/admin/tokens` 铸造永久 bearer token。
- **HIGH-2：限流被 X-Forwarded-For 击穿**。nginx 用 `$proxy_add_x_forwarded_for`（追加），`_client_ip` 取左值 → 客户端每请求换 XFF 即得全新限流桶，5/15min 阈值永不触发（暴力破解无限速）+ 反向锁定 DoS。

外加 11 条 MED/LOW（bcrypt>72字节漏内部信息、数据 hook 绕 apiFetch 导致 401 不重定向、logout 不踢其他 tab、CSRF 仅靠 SameSite=Lax、用户名枚举时序 oracle、session 无上限……）。

### 3. 13 条全修（commit f66543a）

- **must_change 后端强制**：`deps.require_principal` 拒绝 must_change 的 cookie 主体（403）；新增 `require_session_password_set` 给 `/admin/*` + `/docs`；config 公网无 env 时随机生成初始密码（不再裸奔 admin/admin）；seed 日志不打明文
- **X-Real-IP 限流**：`_client_ip` 改信 nginx 设的 `X-Real-IP $remote_addr`（overwrite，客户端无法伪造），不信 XFF
- bcrypt 字节上限 / 数据 hook 走 apiFetch（改造支持 FormData）/ 全局 QueryCache onError 修 in-tab 401 / logout broadcastKick / CSRF Origin-check 中间件（prod，dev 跳过）/ dummy bcrypt 抹平时序 / `prune_sessions_over_cap` / state.from 归一
- 测试改成"先改密激活"+ 新增 must_change→403 断言；**381 passed**

### 4. 公网上线

- 后端经 `gh workflow run deploy.yml --ref feature/refactor-split` 部署到家里小主机（f66543a）
- 前端 `frontend-dist/` scp 到 VPS `/var/www/timetrace.yukirin.me/`
- nginx 重构（版本化 `deploy/nginx-timetrace.yukirin.me.conf`）：托管 SPA + `try_files /index.html` 深链 fallback + 只反代 `/v1 /thumbs /mcp /healthz /docs` + `X-Real-IP $remote_addr`
- 家里 frpc 取消 `timetrace-api` 注释 + 重启
- 公网 e2e 全通：`/healthz` 200、SPA 200、`/v1/records` 无认证 401、`/thumbs` 401、`/mcp` 401、`/docs` 401、admin/admin 401

---

## 遇到的问题与解决

### 问题1：部署后 admin/admin 公网返回 401

**现象**：开公网验证时 `login admin/admin → 401`，DB 查 `users before: [('admin', 0)]`（must_change 已是 0）。
**原因**：用户在浏览器重测 bug#1 时，用**旧的有 bug 版本**改了密码 —— 改密后端成功（must_change 清零），但旧 UI 不显示成功/不跳转，所以用户**不知道自己设成了什么**，登不进去。
**解决**：用户授权重置。家里小主机用 stdlib `sqlite3` 直接 `UPDATE auth_users SET password_hash=?, password_must_change=0`（不跑 full `Database.init()` 以免扰动运行中的 server，WAL 模式并发写安全）+ 清空旧 session。密码重置为用户指定值（本文档脱敏）。公网登录实测 200 + `/v1/records` 200。

### 问题2（贯穿）：must_change 后端强制改变了测试行为

**现象**：加 must_change 强制后，业务/thumbs/admin/docs 测试（登录 admin/admin 后访问）从 200 变 403。
**解决**：测试 helper 改成"login admin/admin → change-password 激活"再访问；并新增 must_change→403 的专门断言。

---

## 知识清单

- **react-query 401 保留 stale data**：出错时 `data` 不清空，必须 `isError ? null : data` 派生登录态；导航前 `await refetch()` 避免读 stale。
- **localStorage `storage` 事件不在写入的 tab 触发**：跨 tab 登出要写者本 tab 自己 `setUser(null)`；且值要唯一（同毫秒同值 = no-op 无事件）。
- **must_change 必须后端强制**：纯前端跳转的强制改密 = 非浏览器客户端可绕过的安全洞。
- **限流别信 X-Forwarded-For**：nginx `$proxy_add_x_forwarded_for` 追加客户端值；改信 `X-Real-IP $remote_addr`（overwrite 不可伪造）。
- **bcrypt 72 字节硬限**：5.x 直接 raise；validate 阶段加字节上限避免漏内部信息。
- **SPA 静态托管需 `try_files $uri $uri/ /index.html`**：否则 BrowserRouter 深链/刷新 404。
- **重置密码不跑 full init**：stdlib sqlite3 直接 UPDATE，WAL 下与运行中 server 并发写安全。
- **多 agent 对抗审计在开公网前价值极高**：揪出 2 个纯人工容易漏的 HIGH。

---

## 待办 / 遗留

- [ ] 密码 `admin998244353` 出现在对话历史里 —— 建议登录后经 Settings→修改密码（现已修复）改成私密值
- [ ] 本机 vite dev + SSH 隧道是 dev 工具，公网开后日常用 `https://timetrace.yukirin.me` 即可，可清理
- [ ] Node.js 20 弃用警告（webfactory/ssh-agent，2026-06-16 截止）—— 低优先
- [ ] CLAUDE.md 的 Auth 段 + DEPLOY-CHECKLIST 待补登录系统说明（之前刻意延后到实装完）
