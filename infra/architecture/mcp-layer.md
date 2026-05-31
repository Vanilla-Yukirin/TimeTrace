# MCP Layer

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

把 TimeTrace 的活动记忆以 **MCP（Model Context Protocol）工具**形式暴露给外部 AI 智能体（Claude Code / Claude Desktop 等），让它们能查询、统计、甚至直接基于活动时间线回答自然语言问题。

- 强制隐私边界：**工具永不返回原始截图**，只返回结构化元数据、VLM 文字描述、时长统计、自然语言回答
- 与 REST API **同进程、同端口、同一条 SSH 隧道**：MCP 挂在 `/mcp`，无需为客户端额外开端口或单独鉴权链路
- 工具实现复用 REST API 同一份 `Database`（同一把 aiosqlite 锁），不另起数据通路

---

## 实现入口

| 项 | 位置 |
|----|------|
| 工具与服务器构建 | `server/mcp_layer/server.py::build_mcp_server(db, vlm_cfg)` |
| 挂载与生命周期 | `server/api/app.py::create_app`（`app.mount("/mcp", mcp_app)` + 自定义 lifespan） |
| 客户端连接配置 | 仓库根 `.mcp.json` |
| 旧版桩函数 | `server/mcp_layer/tools.py`（见下方「tools.py 的历史包袱」） |

`build_mcp_server(db, vlm_cfg)` 返回一个配置好的 `FastMCP` 实例。`create_app` 调 `mcp_server.streamable_http_app()` 拿到 ASGI 子应用并 mount 到 `/mcp`。

---

## 当前工具集（4 个，均已实装）

工具定义在 `build_mcp_server` 内以 `@mcp.tool()` 装饰的闭包形式存在，闭包捕获 `db`（所有工具）与 `vlm_cfg` / `chat_client`（仅 `ask_agent`）。

| 工具 | 签名要点 | 用途 |
|------|----------|------|
| `search_activity` | `query: str, limit=20(≤100), hours_back: int\|None` | 关键词检索活动记录，跨窗口标题 / 应用名 / 进程名 / URL / VLM 描述 |
| `get_recent_activity` | `hours_back=24(≤720), limit=50(≤200)` | 无关键词的最近 N 小时时间序快照，供 agent 概览后再决定深挖 |
| `get_app_breakdown` | `hours_back=24(≤720), top_n=20` | 按应用聚合活跃时长（`SUM(COALESCE(ts_end, ts_start) - ts_start)`），回答「我在 X 上花了多久」 |
| `ask_agent` | `question: str, hours_back=24(≤720)` | 检索 + 推理：取最近活动喂给本地 LLM，直接产出中文自然语言回答 |

返回结构里时间戳统一经 `_ms_to_iso()` 转成本地时区可读字符串（`ts_start_iso` / `ts_end_iso`），方便 LLM 直接阅读，无需再做 epoch 换算。

### 关键词检索的两档策略（`search_activity` / DB 层）

`search_activity` 把 `query` 透传给 `server/db/sqlite.py::query_records(keyword=...)`，由 DB 层按查询长度二选一（阈值 `len(keyword.strip()) >= 3`）：

- **≥ 3 字**：走 `records_fts`（FTS5 **trigram** 分词器）`MATCH` + **BM25** 排序。trigram 对中文友好——「鸣潮」这类多字关键词能正常命中。
- **< 3 字**：trigram 切不出三元组，无法 `MATCH`，回退到**多字段 `LIKE`**。

> 这就是为什么工具 docstring 强调「≥3 char 用 FTS5 trigram + BM25，更短的回退 LIKE」——1~2 字 CJK 查询拿不到 BM25 相关性排序，只能子串匹配。

### `ask_agent` 的设计取舍（依赖 `VLMConfig`）

`ask_agent` 是唯一依赖 LLM 的工具，依赖 `AppConfig.vlm`（`common/config.py::VLMConfig`，由 `TIMETRACE_VLM_*` env 构建）：

- **优雅降级**：`vlm_cfg is None`（未配 API Key）时，工具不报错，返回一句「LLM endpoint not configured」，其余 3 个工具照常工作。
- **单轮 round-trip**：流程是「取最近记录 → 每条压缩成一行 → 一次性喂给本地 Qwen3 模型让它读时间线后作答」，**不做 tool-calling 循环**。这样 demo 延迟可控、也更好调试。
- **上下文预算**：最多取 `_ASK_AGENT_MAX_RECORDS = 80` 条，每条描述截断到 160 字；假设 LM Studio 以 ≥16K 上下文加载模型（默认 4096 容易溢出，部署时需自行 `lms load <model> -c 16384`）。
- **空窗兜底**：请求窗口内无数据时，自动改用 all-time `order="desc"` 再查一次，确保拿到**最近** N 条而不是最老 80 条（这是默认 ASC 排序会踩的坑，已规避），并在回答前加一句「已自动扩窗到 ~Nh」的提示。
- **provider 兼容**：`vlm_cfg.disable_thinking` 为真时注入 `extra_body={"enable_thinking": False}`；LM Studio 把 Qwen3 输出塞进 `reasoning_content`，故答案提取用 `content or reasoning_content` 兜底（与 `vlm/client.py` 同款回退）。
- **连接池复用**：`AsyncOpenAI` 客户端按 server 缓存（不是每次调用新建），httpx 连接池跨请求复用。

---

## 挂载与生命周期：为什么 lifespan 必须驱动 session_manager

`create_app` 里这段是 MCP 能跑起来的关键（`server/api/app.py`）。注意构建顺序：**先 `build_mcp_server`，再用它的 `session_manager` 拼出 lifespan，把 lifespan 传进 `FastAPI(...)` 构造器**，最后才 `mount`：

```python
mcp_server = build_mcp_server(db, vlm_cfg)

@asynccontextmanager
async def lifespan(_app):
    async with mcp_server.session_manager.run():   # ← 必须
        yield

app = FastAPI(..., lifespan=lifespan)
...
mcp_app = mcp_server.streamable_http_app()
mcp_app = BearerOnlyMiddleware(mcp_app, auth)     # bearer-only 见下节
app.mount("/mcp", mcp_app)
```

FastMCP 的 streamable HTTP 传输自带一个 anyio task group（即「session manager」）。**这个 task group 必须在应用生命周期内被进入/退出**，否则每个 MCP 请求都会 500 报 `Task group is not initialized`。即便我们用 `stateless_http=True`（每个 HTTP 请求独立、无 session id / event store），`session_manager.run()` 仍然得从 FastAPI lifespan 里驱动——所以这个 lifespan 在构造 `FastAPI` 时就传进去，由 FastAPI 接管，`mount` 只负责挂 ASGI 子应用。

`FastMCP` 的三个非默认构造参数（`server.py::build_mcp_server`）：

| 参数 | 值 | 原因 |
|------|----|------|
| `stateless_http` | `True` | 每请求独立，无可恢复流；对「调一个工具拿一段 JSON 就完」的用法更简单 |
| `json_response` | `True` | 返回纯 JSON 而非 SSE 流，curl 可调、契合单发工具客户端 |
| `streamable_http_path` | `"/"` | FastMCP 默认是 `/mcp`，会和我们 FastAPI 的 `/mcp` mount 点叠加成 `/mcp/mcp/`；设为根，客户端直接连 `/mcp/` |

---

## 鉴权：为什么 `/mcp` 是 bearer-only

登录系统（cookie session）是给浏览器 Web UI 用的；MCP 客户端（Claude Code / Desktop）是程序化调用，**只走 bearer token**（`server/auth.py::ServerAuth`，`tt_live_` 前缀）。换言之：

- `/mcp` 与 `/v1/ingest/*` 同属「机器对机器」面，用 bearer token 鉴权，不接 cookie 登录态。具体实现：mount 前用 `BearerOnlyMiddleware(mcp_app, auth)` 把整个 MCP 子应用裹一层 ASGI 中间件，统一校验 `Authorization: Bearer tt_live_...`。
- `/healthz` 与面向前端的 records / search / feedback 路由不要 token。
- 本地默认监听 `127.0.0.1`，MCP **不直接对公网开放**；公网访问统一走 nginx 反代 + frp 隧道 + 鉴权之后再说。

`.mcp.json`（仓库根，本机连法）：

```json
{
  "mcpServers": {
    "timetrace": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp/"
    }
  }
}
```

---

## 隐私边界

| 约束 | 说明 |
|------|------|
| **不返回原始截图** | 4 个工具只返回文本描述 / 元数据 / 统计 / 自然语言回答，绝不含图片 URL 或 base64 |
| **数量上限** | 每个工具都对 `limit` / `top_n` / `hours_back` 做 `min(max(...))` 夹取，防止一次塞给模型海量帧 |
| **本地监听** | API 默认 `127.0.0.1`，MCP 随之不对外网暴露 |
| **bearer 鉴权** | 机器面统一 bearer token，不复用浏览器 cookie 登录态 |

---

## tools.py 的历史包袱

`server/mcp_layer/tools.py` 里还留着一组早期（Phase 1.5）独立的 async 工具函数：`list_categories` / `get_activity` / `get_category_stats` / `search_activity`。其中 `get_category_stats` 与 `search_activity` 仍是**返回桩数据**的占位实现，且 `get_category_stats` / `list_categories` 依赖一张 `categories` 表。

**这组函数已不是当前 MCP 的对外接口**——真正暴露给 agent 的是 `server.py::build_mcp_server` 里那 4 个 `@mcp.tool()`。tools.py 视为待清理的遗留模块，新功能一律加到 `server.py`。

---

## MCP 规范参考

MCP（Model Context Protocol）是开放协议，用于把外部数据源与工具接入 LLM 应用生态。TimeTrace 通过 `FastMCP` 实现 streamable HTTP 传输，按规范定义 tools，作为外部智能体读取桌面活动记忆的统一入口。

---

## 相关文档

- [Local API Server（MCP 复用同一服务层）](api-server.md)
- [Analysis Worker（VLM 描述写回，喂给 ask_agent 的素材）](analysis-worker.md)
- [架构总览](overview.md)
