# 登录系统实现：Phase 1-6（admin/admin → 强制改密 → cookie session + bearer 双通道）

**日期：** 2026-05-30
**目标：** 公网暴露止血后，建一套单用户登录系统永久解决 auth。设计定稿见 [archive-202605280400-login-system-design](archive-202605280400-login-system-design.md)，本篇记录 Phase 1-6 的实际实现。

---

## 背景

用户要"和 grafana 一样的登录系统"：默认 admin/admin → 首次强制改密 → 单用户（暂不做多用户/数据分区/浏览器配 VLM key，全砍）。两条并行 auth 通道：**Cookie session**（浏览器/人）+ **Bearer token**（MCP/采集端/脚本，继承现有 ServerAuth）。本工作流被主 agent 称为 "infra agent"，与并发的 embedding 工作流共用 feature/refactor-split 分支、刻意避开 app.py 冲突。

三个开工前确认的小决策：① `/docs` + `/openapi.json` 也 cookie 保护（不给扫描器送 API 地图）② admin 用户名走 `TIMETRACE_ADMIN_USERNAME` env，默认 admin ③ 本地 dev 用 `TIMETRACE_INSECURE_COOKIE=1` 切 cookie Secure flag。

---

## 操作步骤（每 phase 独立 commit + 全量 pytest）

### Phase 1 — 后端 auth 核心（commit 268a04c）

- DB 加两张表 `auth_users`（username PK / password_hash / password_must_change）+ `auth_sessions`（id / username / expires_at / user_agent / ip），幂等 migration，不动现有 8 张业务表（单用户无 user_id 分区）
- `common/config.py::AuthConfig`（from_env 读 admin_username + cookie_secure）
- `server/users.py::UserStore`：bcrypt(cost=12) 哈希 + session 铸造 + 内存 per-IP 失败限流（5 次/15min → 锁 30min）+ 密码强度校验（8+ / 字母+数字）+ `ensure_admin_seeded` 首启幂等 seed + `change_password`（改密时 `delete_sessions_except` 踢其他设备）
- `server/api/routes/auth.py`：`/v1/auth/login|logout|me|change-password`
- `server/api/deps.py::require_session`（仅 cookie）
- bootstrap seed admin + reclaim loop 加 `purge_expired_sessions`
- `/docs` + `/openapi.json` 改 `Depends(require_session)` 保护（create_app 加 `docs_url=None` 再手动重挂）
- 依赖 + `bcrypt>=4.0.0`
- 测试 +29，全量 301 passed

### Phase 2 — /thumbs 改造 + require_principal（commit 75ad46e）

- 陷阱：`StaticFiles.mount` 是独立 ASGI app，绕过 FastAPI Depends 链 → 删 mount，改 `routes/thumbs.py` 自定义 `FileResponse` 路由 + path-traversal 防御（`resolve()` + `relative_to()` 容器检查）+ `Cache-Control: private`
- `SessionUser → CookiePrincipal`（语义对齐）+ `auth.py::BearerPrincipal(token_label)` + `ServerAuth.find_label()`
- `deps.py::require_principal`（cookie 优先 → bearer）+ `Principal = CookiePrincipal | BearerPrincipal` union → 路由能审计区分"人看"vs"机器拉"
- 修 Phase 1 隐患：bootstrap admin_seed log 不打明文密码，改 `password_default=True/False`
- 测试 +10（含 ../ 越狱、cookie 优先于乱 bearer），全量 311 passed

### Phase 3 — /mcp BearerOnlyMiddleware（commit 85e84c5）

- 陷阱：app.mount 绕 Depends；且**不能用 BaseHTTPMiddleware**（它物化 response body，破坏 streamable_http 的 SSE/chunked 流）
- 解法：**纯 ASGI middleware**（`__call__(scope,receive,send)`），只 gate `scope["type"]=="http"`，lifespan/websocket passthrough（session_manager.run() 依赖 lifespan 完整流到内层）
- 必须 **mount 之前 wrap**（starlette 子 app mount 后 middleware stack 冻结）
- bearer-only（MCP 客户端不是浏览器，cookie 不解锁）
- 测试 +6（含 lifespan 完整性验证），全量 330 passed

### Phase 4 — 业务路由套 require_principal（commit 9524450）

- `include_router(..., dependencies=[Depends(require_principal)])` 一次套定 records/search/feedback/categories/runtime-info/apps
- `users=None` 时 `business_deps=[]`（保留老 `create_app(db)` 测试路径无 auth 语义，和 `auth=None → ingest 开放`同形）
- 测试 +24（parametrize 5 endpoint × 4 通道 + guard），全量 368 passed

### Phase 5 — 前端登录流 + .gitignore 大坑（commit cf9ec9c）

- `lib/api.ts`：`credentials:'include'` + `UnauthorizedError`(401 拦截) + `broadcastKick()` 跨 tab 广播 + login/logout/me/changePassword
- `contexts/AuthContext.tsx`：`useQuery(/v1/auth/me)` + refetchOnWindowFocus + storage 事件跨 tab 登出同步
- `components/RequireAuth.tsx`：loading→spinner / 未登→/login / must_change→/login/change-password / 正常→渲染
- `pages/LoginPage.tsx` + `pages/ChangePasswordPage.tsx`（本地强度校验镜像后端）+ TopBar 加用户名 + 退出
- **顺手修 .gitignore 陷阱**：Python 模式 `lib/` 无锚点，把 `frontend/src/lib/` 整个吞了 → api.ts/queryKeys.ts/colorMap.ts/dateUtils.ts/utils.ts **从未入库，fresh clone 构建不了**。改成 `/lib/` `/lib64/` 锚定仓库根 + 补回 5 个文件
- vite build 0 error

### Phase 6 — Settings 账户区 + Tokens CRUD（commit 5bd4071）

- `server/api/routes/admin.py`：`/v1/admin/tokens` GET/POST/DELETE（cookie-only，bearer 不解锁管理操作）
- `ServerAuth` 加实例 `add_token`/`revoke_token`：改内存集合 + 持久化，**即时生效不重启**（区别于 admin_cmd.py CLI 的"写文件后重启"）；`__init__` 记 token_dir
- 前端 `components/admin/`：AccountSection（用户名+改密+退出）+ TokenManager（list/create/revoke）+ TokenCreatedDialog（创建一次性弹窗，内置可复制 token 值 + 完整 `.mcp.json` 模板，url 用 `window.location.origin` 自适配 + Claude Code 接入三步说明）
- SettingsPage 加两区，runtime-info loading 只影响"后端状态"块不遮 auth 控件
- 测试 +13（含热生效/热撤销），后端 373 passed；vite build 0 error

---

## 遇到的问题与解决

### 问题1：StaticFiles / mount 绕过 Depends（两个陷阱）

`/thumbs`（StaticFiles）和 `/mcp`（FastMCP mount）都是独立 ASGI 子 app，FastAPI 的 `dependencies=[Depends(...)]` 不传导。解法不同：/thumbs 重写成普通 FileResponse 路由（能套 Depends）；/mcp 必须用纯 ASGI middleware 在 mount 前 wrap（保 streaming）。

### 问题2：.gitignore `lib/` 吞掉前端源码

`git check-ignore -v frontend/src/lib/api.ts` → `.gitignore:17:lib/`。Python venv 的 `lib/` 模式无锚点，递归匹配任何 lib 目录。这些文件本地存在、build 用，但从没进 git。锚定 `/lib/` 修复 + 补提交。

### 问题3：测试期望 vs 环境依赖失败的甄别

全量跑出 2 个 fail：`test_rrf_weights_bias_channel`（embedding 工作流的 RRF tie-break 测试期望写错，主 agent 已在 f34651e 修，非我代码）+ `test_vlm_smoke`（打真实 VLM 端点，Qwen 当时被卸了返空）。都 deselect 确认与登录系统无关。

---

## 知识清单

- **双通道 auth**：cookie（人，require_session 给改密等敏感操作）+ bearer（机器，require_principal 给数据路由）。两个 Principal dataclass 让路由审计区分来源。
- **bearer 不解锁管理操作**：admin tokens CRUD 只认 cookie —— 持 token 的脚本不该能给自己改密/管理 token。
- **FastMCP + auth**：纯 ASGI middleware 不破坏 streaming；BaseHTTPMiddleware 会。mount 前 wrap。
- **token 热生效**：Web UI 创建/撤销直接改运行中的 ServerAuth 内存集合 + 持久化，无需重启；CLI（admin_cmd.py）是写文件后重启。两条路径都写同一个 tokens.json。
- **Secure cookie 默认 True**（生产 HTTPS 正确），本地 dev `TIMETRACE_INSECURE_COOKIE=1` 关掉。

---

## 待办 / 遗留

- [x] Phase 1-6 全部 commit + push（origin/feature/refactor-split）
- [ ] Phase 7 部署 + 隧道验证（见 [archive-...-phase7-deploy](archive-202605300222-phase7-deploy-qwen-restore.md)）
- [ ] 公网开放（用户验证登录 UX 后取消 frpc 注释）
- [ ] 设计文档提到的 Origin header CSRF 校验未实装（SameSite=Lax 兜底，单用户够）—— 已知 gap
