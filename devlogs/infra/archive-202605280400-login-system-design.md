# 登录系统 + 公网部署 auth 设计（2026-05-28）

**状态**：设计完成，待实施（Phase 1-7，估 4-5 天）
**触发事件**：2026-05-27 夜间将 TimeTrace 反代到公网域名 `timetrace.yukirin.me`，发现 `/v1/records` / `/v1/search` / `/v1/feedback` / `/mcp/` / `/thumbs/*` 5 个面向浏览器/AI 的路由全部公开，**所有桌面截图与 VLM 描述对公网裸奔**。
**临时止血**：注释 home frpc 的 `[[proxies]] timetrace-api` 段并重启 `frpc@xcy.service`，公网回 502。`nginx` / DNS / LE 证书 / `frps` 全保留，等本设计实施完成后取消注释即可复活公网入口。

---

## 1. 背景与问题陈述

### 当前 v1 auth 模型

- `server/auth.py::ServerAuth`：**单一 Bearer token 池**，token 落 `~/.config/timetrace-server/tokens.json`（POSIX chmod 600）
- 只有 `/v1/ingest/*` 路由套 `Depends(bearer)`（capture client → server 的机器到机器通道）
- 其余 5 个浏览器/AI 通道**完全公开**，假设是 loopback only

### 公网部署后真实威胁模型

| 路由 | 现状 | 公网下后果 |
|---|---|---|
| `GET /v1/records` | 公开 | 任何人爬 → 拿到 records + VLM 描述（详细窗口标题、自然语言概要） |
| `GET /v1/records/{id}` | 公开 | 单条详情含 screenshots 元数据与 thumb 路径 |
| `GET /v1/search/*` | 公开 | 按关键字 / 图搜回历史活动 |
| `POST /v1/feedback` | 公开 | 任何人能改你的 category / tag |
| `/mcp/*`（FastMCP mount） | 公开 | 任何人能调 `ask_agent` 等工具消耗 VLM 配额 + 拿数据 |
| `/thumbs/*`（StaticFiles mount） | 公开 | **直接下载所有缩略图** |

公网暴露 = 数据泄露 + 资源被滥用，必须封死。

### 但不能简单"全部加 bearer"

1. **MCP 客户端不是浏览器**：Claude Code 走 `.mcp.json` 配置 headers，必须保留 Bearer 通道
2. **本地 Vite dev (`5173`) → API (`8765`)** 现在零摩擦，要保持本地 dev 不破
3. **本地 frontend 也要给"密码登录"**：用户明确要求 admin/admin → 强制改密 → 单用户登录的安全感
4. **localStorage JWT 有 XSS 偷窃风险**：MCP token 必须由 admin UI 创建/管理，存到 localStorage 等于双重门洞

---

## 2. 设计决策

### 2.1 双通道 auth：Cookie + Bearer 并存

| 通道 | 谁用 | 验证方式 | 创建方式 |
|---|---|---|---|
| **Cookie session** | 浏览器（人） | HttpOnly cookie 含 `session_id`，server 查 `auth_sessions` 表 | `POST /v1/auth/login`（用户名+密码换 cookie） |
| **Bearer token** | MCP 客户端 / capture client / 脚本 | `Authorization: Bearer tt_live_...` | admin 在 Web UI 创建（继承现有 `ServerAuth` + `tokens.json`） |

**关键**：浏览器路由（records/search/feedback 等）**接受任一通道**，写成单一 dependency `require_cookie_or_bearer`。/mcp 与 /ingest **只接受 bearer**（机器对机器）。/v1/auth/* 自己就是 auth 路由，不套 deps。

### 2.2 单用户，无 role / 无 users CRUD

- `auth_users` 表只保证一行 `admin`
- **不加 `role` 列** —— 单用户场景下 `role` 是 YAGNI；未来真多用户时 migration 加列零成本
- **不加 admin 邀请 / users CRUD 路由** —— 用户明确说"是唯一用户"
- 所有数据无 `user_id` 字段，沿用现有"机主拥有一切"模型

### 2.3 Session 存储：HttpOnly Cookie + DB 表（不用 JWT）

| 方案 | 选 | 否决理由 |
|---|---|---|
| **HttpOnly Cookie + `auth_sessions` 表** | ✅ | XSS 偷不走；可立刻 revoke；改密一键踢所有设备 |
| localStorage + JWT | ❌ | XSS 风险；revoke 难（要 blocklist）；单用户场景省不下复杂度 |
| 内存 session | ❌ | 重启即失效，体验差 |

Cookie 属性：`HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=30d`，滑动续期（每次请求延长）。

### 2.4 本地 dev: env var 切 Secure flag

Cookie `Secure` 标志只在 HTTPS 下生效。本地 Vite dev 是 HTTP。

- server 读 `TIMETRACE_INSECURE_COOKIE=1` 时**不加 `Secure`**
- 单进程模式（`timetrace`）默认本地，自动注入该环境变量
- 双进程 server (`timetrace-server`) 默认要求 HTTPS（即不读这个 env 时强制 Secure）；本地起 dev server 需用户手动 `TIMETRACE_INSECURE_COOKIE=1 uv run timetrace-server`

### 2.5 CSRF 防护：SameSite=Lax + Origin 校验

- `SameSite=Lax` 由浏览器保证大部分跨站场景 cookie 不发送
- 所有写操作（POST/PUT/DELETE）服务端**额外校验 `Origin` header** 在白名单内（默认 `https://timetrace.yukirin.me` + 本地 dev origin）
- **不引入 CSRF token**，简单优先

### 2.6 强制改密流程

1. 首次启动 server 检测 `auth_users` 空 → 插入 `admin / bcrypt("admin") / must_change=1`
2. admin/admin 登录 → 200 + cookie + body `{ must_change_password: true }`
3. 前端检测 must_change → 路由守卫强制跳 `/login/change-password`
4. 改密成功后 `must_change=0`，且**所有其他 session 失效**（防初始密码已被偷的场景）

新密码强度：8+ 字符，含字母+数字。**不强制特殊符号**（自用，反爆破靠限流不靠复杂度）。

### 2.7 登录失败限流

5 次失败 / 15 分钟 / 一个 IP → 封 30 分钟。用内存 dict 即可（单实例）。

### 2.7.1 两个小决策（2026-05-29 确认）

- **`/docs` + `/openapi.json` 走 cookie 保护**（不只 read-only public）。理由：成本 5 行，收益是不给公网扫描器送一份 API 地图。本地 dev 登录后正常能看
- **admin 用户名通过 env 自定义**：读 `TIMETRACE_ADMIN_USERNAME`，未设走默认 `"admin"`。理由：`username` 是 PRIMARY KEY 改不了，第一次必须对；env 比硬编码灵活、比 UI 设置流程简单。机主可首启就用 `yuki`，开源 fork 用户用默认

### 2.8 不做的事（明确边界）

| 砍掉项 | 原因 |
|---|---|
| 数据按 user_id 分区 | 单用户，YAGNI |
| 用户邀请 / 注册 / 多账号 | 单用户，YAGNI |
| 浏览器配置 VLM API key 等 | 你能 SSH 编 `.env`，DB 化整套 config = 多 1-2 天 + 引入 secret 加密存储争议，scope creep |
| "记住我"复选框 | 默认就 30 天 cookie，复选框是多余 toggle |
| 密码找回邮件 | 没人收邮件，admin 自己拿不到密码 → SQLite 里手动 update |
| TOTP / 2FA | 自用 overkill；公网+强密码+限流已够 |

---

## 3. 数据库 Schema

```sql
CREATE TABLE auth_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    password_must_change INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,           -- epoch ms
    updated_at INTEGER NOT NULL
);

CREATE TABLE auth_sessions (
    id TEXT PRIMARY KEY,                    -- secrets.token_urlsafe(32)
    username TEXT NOT NULL REFERENCES auth_users(username),
    created_at INTEGER NOT NULL,            -- epoch ms
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    user_agent TEXT,                        -- 用于 settings 页"已登录设备"
    ip TEXT                                 -- 同上
);
CREATE INDEX idx_sessions_username ON auth_sessions(username);
CREATE INDEX idx_sessions_expires  ON auth_sessions(expires_at);
```

**不动其他 8 张现有表**（records / screenshots / analysis_results / categories / tags / record_tags / feedback / settings）。

**Migration**：在 `SqliteDatabase._migrate` 现有逻辑追加两张表的 `CREATE TABLE IF NOT EXISTS`。首次启动新表为空 → bootstrap 时检测并 seed `admin / bcrypt("admin") / must_change=1`。

---

## 4. 后端路由设计

### 4.1 新增

| Method | Path | 用途 | Auth | Body |
|---|---|---|---|---|
| `POST` | `/v1/auth/login` | 登录 | — | `{ username, password }` → 200 + Set-Cookie; body `{ must_change_password }` |
| `POST` | `/v1/auth/logout` | 销毁当前 session | cookie | — → 204 |
| `GET` | `/v1/auth/me` | 当前用户态 | cookie | → `{ username, must_change_password }` |
| `POST` | `/v1/auth/change-password` | 改密 | cookie | `{ old_password, new_password }` → 204；副作用：失效所有其他 session |
| `GET` | `/v1/admin/tokens` | 列 Bearer token | cookie | → `[{ label, created_at, last_used_at? }]`（**不返回 value**） |
| `POST` | `/v1/admin/tokens` | 创建新 token | cookie | `{ label }` → `{ label, value }`（value **仅此一次**返回） |
| `DELETE` | `/v1/admin/tokens/{label}` | 撤销 token | cookie | → 204 |

### 4.2 现有改造

| Path | 现状 | 改后 |
|---|---|---|
| `/v1/records`, `/v1/records/{id}`, `/v1/apps`, `/v1/runtime-info`, `/v1/search/*`, `/v1/feedback`, `/v1/categories` | 公开 | **cookie 或 bearer** |
| `/v1/ingest/*` | bearer | 不变 |
| `/mcp/*` | 公开（FastMCP mount） | **bearer**（starlette middleware，**见 §6**） |
| `/thumbs/*` | StaticFiles mount 公开 | **cookie 或 bearer**（**StaticFiles 不能套 Depends，必须换 FileResponse，见 §5**） |
| `/healthz` | 公开 | 不变（运维探活） |
| `/docs`, `/openapi.json` | 公开 | **cookie**（admin 才能看 OpenAPI；公网 schema 不应裸露） |

### 4.3 Dependency 抽象

新增 `server/api/deps.py`：

```python
def require_session(...) -> SessionUser  # cookie only
def require_bearer(...) -> None           # bearer only（替代 make_bearer_dependency 单一通道版）
def require_session_or_bearer(...) -> SessionUser | None  # 二选一，业务路由用
```

`SessionUser` dataclass: `{ username, session_id, must_change_password }`。路由用 `Depends(require_session_or_bearer)` 取到则放行；返回 None 表示 bearer 通道（仍合法，但路由若需要 username 则 fallback 用机器身份字符串如 `"<bearer>"`）。

---

## 5. `/thumbs/*` 改造（隐蔽陷阱 1）

### 问题

```python
# 现状（app.py）
app.mount("/thumbs", StaticFiles(directory=storage_cfg.thumbs_dir))
```

`StaticFiles` 是独立 ASGI app，**绕开 FastAPI 中间件与 Depends**。直接换成 `Depends(require_session_or_bearer)` 不生效。

### 解决方案

定义自定义路由：

```python
# server/api/routes/thumbs.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

router = APIRouter()

@router.get("/thumbs/{path:path}")
async def get_thumb(
    path: str,
    request: Request,
    _user = Depends(require_session_or_bearer),
):
    # 防路径穿越
    thumbs_dir: Path = request.app.state.thumbs_dir
    resolved = (thumbs_dir / path).resolve()
    if not resolved.is_relative_to(thumbs_dir.resolve()):
        raise HTTPException(404)
    if not resolved.is_file():
        raise HTTPException(404)
    return FileResponse(resolved, headers={
        "Cache-Control": "private, max-age=86400",  # 私有缓存（不让 CDN 缓存）
    })
```

`app.state.thumbs_dir` 存 Path 对象（之前是 str data_dir）。

**性能注意**：每个 thumb 请求都过 Python，多过一次 DB session 查（cookie 路径）或 bearer set 查（O(1)）。前端时间轴一屏 ~30 张 thumb，性能开销可接受（< 10ms 总）。如未来证明瓶颈，可换 nginx 子路径 `auth_request` 模式。

---

## 6. `/mcp/*` 改造（隐蔽陷阱 2）

### 问题

```python
# 现状（app.py）
app.mount("/mcp", mcp_server.streamable_http_app())
```

FastMCP 的 `streamable_http_app()` 是 starlette 子 app。`app.mount(...)` 后 FastAPI 的 dependency_overrides 与 `dependencies=[...]` 不传导。

### 解决方案

用 starlette middleware **包裹子 app 后再 mount**：

```python
from starlette.middleware.base import BaseHTTPMiddleware

class BearerOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, auth: ServerAuth):
        super().__init__(app)
        self._auth = auth

    async def dispatch(self, request, call_next):
        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not token or not self._auth.is_valid(token):
            return JSONResponse({"detail": "missing or invalid bearer"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

mcp_app = mcp_server.streamable_http_app()
mcp_app.add_middleware(BearerOnlyMiddleware, auth=auth)
app.mount("/mcp", mcp_app)
```

**风险点**：
- middleware 必须 **在 mount 之前 add**（starlette 限制 — mount 后 middleware stack frozen）
- 不能破坏 FastMCP 的 `session_manager.run()` lifespan（其 lifespan 由外层 FastAPI 驱动，见 [app.py:42-46](src/timetrace/server/api/app.py#L42-L46)）
- SSE / long-lived 连接：middleware 只在请求开始时验证一次，长连保持期间不再校验（标准做法）

### 测试要点

- `pytest`：`POST /mcp/` 无 token → 401
- `pytest`：带 valid bearer → 转发到 FastMCP 正常返回
- 现有 `ask_agent` / `get_app_breakdown` 等工具调用正常（不破坏 streamable_http 行为）

---

## 7. 前端架构

### 7.1 新增

```
frontend/src/
  contexts/
    AuthContext.tsx           ← user state + login/logout/refresh
  pages/
    LoginPage.tsx             ← 用户名 + 密码
    ChangePasswordPage.tsx    ← 旧密码 + 新密码 + 新密码确认
  components/
    RequireAuth.tsx           ← 路由守卫
    admin/
      TokenManager.tsx        ← Settings 页面新区块：list / create / revoke
      TokenCreatedDialog.tsx  ← 创建成功弹窗，包含 .mcp.json 模板
  lib/
    api.ts                    ← 改：credentials: 'include' + 401 拦截 + cross-tab 同步
```

### 7.2 路由

```tsx
<Routes>
  <Route path="/login" element={<LoginPage />} />
  <Route path="/login/change-password" element={
    <RequireAuth allowMustChange><ChangePasswordPage /></RequireAuth>
  } />
  <Route element={<RequireAuth><MainLayout /></RequireAuth>}>
    <Route path="/" element={<TimelinePage />} />
    <Route path="/search" element={<SearchPage />} />
    <Route path="/settings" element={<SettingsPage />} />
  </Route>
</Routes>
```

`RequireAuth` 调 `GET /v1/auth/me`：
- 401 → redirect `/login`
- `must_change_password=true` 且当前不在 `/login/change-password` → redirect 过去
- 否则渲染子节点

### 7.3 401 拦截 + Cross-tab 同步

```ts
// lib/api.ts
async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    credentials: 'include',  // 关键：发送 cookie
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  })
  if (res.status === 401) {
    localStorage.setItem('tt_auth_kicked', String(Date.now()))  // 广播给其他 tab
    window.location.href = '/login'
    throw new Error('401 unauthorized')
  }
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`)
  return res.json() as Promise<T>
}

// AuthContext.tsx 监听 storage 事件
useEffect(() => {
  const handler = (e: StorageEvent) => {
    if (e.key === 'tt_auth_kicked') window.location.href = '/login'
  }
  window.addEventListener('storage', handler)
  return () => window.removeEventListener('storage', handler)
}, [])
```

### 7.4 Settings 页面新区块

- **Account**：username 只读、改密码按钮（打开 modal）、退出登录按钮
- **API Tokens**：
  - 表格列：label / created_at / 撤销按钮
  - "+ 新建 token" 按钮 → 模态框输入 label → 提交后弹 `<TokenCreatedDialog>` 显示 value + .mcp.json 模板（见 §8），用户复制完点"我已保存"才能关闭

---

## 8. MCP 客户端接入流程（必须写清！）

token 创建成功的 `<TokenCreatedDialog>` 必须包含**完整可复制的 .mcp.json 模板**：

```jsonc
{
  "mcpServers": {
    "timetrace": {
      "type": "http",
      "url": "https://timetrace.yukirin.me/mcp/",
      "headers": {
        "Authorization": "Bearer tt_live_<your_token>"
      }
    }
  }
}
```

外加文字提示：
> 此 token value **仅此一次显示**，请立即复制保存。
>
> 1. 将上面模板粘贴到 Claude Code 项目根的 `.mcp.json`
> 2. 重启 Claude Code（VSCode / Terminal）
> 3. `claude mcp list` 应看到 `timetrace`

---

## 9. 实施顺序（每步可工作不留半成品）

| Phase | 内容 | 估时 | 验收 |
|---|---|---|---|
| **1. 后端 auth 核心** | 2 张表 migration + bcrypt + login/logout/me/change-pw 路由 + cookie session middleware + 初始 admin/admin seed | 0.5 天 | `pytest` + `curl` 流程：POST login → 200 + cookie → POST change-password → GET me 显示 must_change=0 |
| **2. `/thumbs` 改造** | StaticFiles → FileResponse + path-traversal 防护 + auth | 0.5 天 | 前端 timeline 缩略图能正常显示；curl 不带 cookie/bearer → 401；现有缩略图 URL 不变 |
| **3. `/mcp` middleware** | BearerOnlyMiddleware + 测试现有 `ask_agent` 工具不挂 | 0.5 天 | `pytest`：无 token 401、有 token 通；本地起 `claude mcp list` 仍 OK |
| **4. 业务路由 auth 接入** | records / search / feedback / categories / runtime-info 全套 `Depends(require_session_or_bearer)` + Origin 校验中间件 | 0.5 天 | `pytest` 全部 401 → 200 切换正常 |
| **5. 前端登录** | LoginPage + ChangePasswordPage + AuthContext + RequireAuth + lib/api.ts 401 拦截 + cross-tab | 0.75 天 | 本地走完一遍 admin/admin → 改密 → timeline 页 |
| **6. 前端 Settings** | Account 区 + TokenManager + TokenCreatedDialog 含 .mcp.json 模板 | 0.5 天 | 创建 token → 复制 value → 配 .mcp.json → Claude Code 接入成功 |
| **7. e2e + 恢复公网** | 通过 SSH 隧道 e2e 全链路；frpc 取消注释 `[[proxies]] timetrace-api`；公网 `curl https://timetrace.yukirin.me/v1/records` 无 cookie 401 / 浏览器登录后 200 | 0.75 天 | 上述 4 条 |

总：**~4 天**（顺利），**~5 天**（含 corner case 反复）。

每步完成后 commit + push + 跑全量 `pytest`。

---

## 10. 现有 `ServerAuth` / `tokens.json` 兼容性

- **保留** `server/auth.py::ServerAuth` 与 `~/.config/timetrace-server/tokens.json`
- 现有 capture client 的 token 继续工作（这是 P3b 的契约）
- `timetrace-server tokens` CLI 子命令保留（admin 也能命令行管理 token，等于 Web UI 的 backdoor）
- Web UI 的 token CRUD **包装** `ServerAuth.write_tokens()` 即可，不引入第二个 token 存储

---

## 11. 测试矩阵

| 测试文件 | 新增覆盖 |
|---|---|
| `tests/test_auth_users.py`（新） | bcrypt hash / verify，must_change 字段流转 |
| `tests/test_auth_sessions.py`（新） | session 创建 / 校验 / 过期 / revoke / 改密时失效所有其他 session |
| `tests/test_auth_routes.py`（新） | login/logout/me/change-password 完整流程，限流，CSRF Origin 校验 |
| `tests/test_thumbs_auth.py`（新） | StaticFiles 替换后的 path-traversal + auth |
| `tests/test_mcp_auth.py`（新） | BearerOnlyMiddleware 行为 + FastMCP 流式响应不破 |
| `tests/test_api.py`（改） | 现有路由全要带 cookie 或 bearer |

预计新增 ~40 个 test cases，全跑约 3-5s。

---

## 12. 未来扩展点（不在本次范围）

| 场景 | 实施路径（届时） |
|---|---|
| 多用户 | `auth_users` 加 `role` 列 + 所有数据表加 `user_id` + capture 端身份化 + admin 邀请路由 |
| TOTP / 2FA | `auth_users` 加 `totp_secret` 列 + login 流程加二步 |
| 浏览器配 VLM key 等 config | 配置 DB 化 + admin settings 页 + 加密字段（secret_box）+ 热重载 worker |
| OAuth / SSO | 单独 `/v1/auth/oauth/...` 路径，session 通道复用 |
| 公网额外硬化 | CF 橙云接 DDoS 防护（接受 CF 解密明文的代价）/ fail2ban / Caddy mTLS / WAF |

---

## 13. 关联

- 触发事件：见本仓库 git log 5/27 后部署 + 5/28 凌晨公网验证
- 协议契约：[`devlogs/infra/archive-202605151200-client-server-split-kickoff.md`](archive-202605151200-client-server-split-kickoff.md) P3b auth 段（现行 token 体系）
- 部署架构：[`devlogs/infra/archive-202605161000-deployment-architecture.md`](archive-202605161000-deployment-architecture.md)
- 代码现状：[`src/timetrace/server/api/app.py`](../../src/timetrace/server/api/app.py)、[`src/timetrace/server/auth.py`](../../src/timetrace/server/auth.py)
- 前端 API 入口：[`frontend/src/lib/api.ts`](../../frontend/src/lib/api.ts)、[`frontend/src/App.tsx`](../../frontend/src/App.tsx)
