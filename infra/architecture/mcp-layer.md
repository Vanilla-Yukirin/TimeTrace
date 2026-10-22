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

## 当前工具集

工具以 `build_mcp_server` 内 `@mcp.tool()` 装饰的闭包形式注册，真实读写实现复用 `server/agent/tools.py` 与 `server/agent/activity_context.py`。MCP 与 Web Agent 共享读取工具（包括 `query_stats`）；MCP 另有高层 `ask_agent`。它复用 Web Agent 的 `AgentRunner` 工具循环（最多 4 轮）。

`apply_label` 是**唯一写面**（只改 `category_final` + 写 feedback 审计行，绝不删改记录/截图），其余工具只读。

| 工具 | 签名要点 | 用途 |
|------|----------|------|
| `get_latest_activity` | `device_id?` | 每设备最新采集记录、采集年龄、首次入库时间、最后联系时间、分析状态；不是用户在线探针 |
| `get_activity_overview` | `period, start_iso/end_iso, device_id?, max_grain=day, limit=50` | 每设备粗到细摘要回退，返回非重叠窗、覆盖缺口和截断状态；不现场调用模型 |
| `list_devices` | 无参数 | 设备 ID、名称、描述，供其他读工具选择设备范围 |
| `query_stats` | `metric, period, device_id?` | 时间/条数聚合；支持按设备统计和跨设备墙钟并集 |
| `search_activity` | `query: str, limit=20(≤2000), hours_back/start_iso/end_iso` | 关键词 + 语义（embedding）RRF 融合检索，走 `server/retrieval.py::retrieve_records`（与 `/v1/search/text`、Web Agent 同一路径）；附 `related_summaries` |
| `get_recent_activity` | `hours_back=24(≤720), limit=50(≤2000), cursor, order=asc` | 原始记录分页；明确 `has_more`，固定查询窗/入库高水位，按 `(ts_start,id)` 稳定排序，支持 DESC |
| `get_app_breakdown` | `period?(today/this_week/this_month/custom), hours_back=24(≤720), top_n=20` | 按应用聚合活跃时长（`SUM(clamped duration)`：单条记录时长经 `_clamped_dur_sql` 封顶——负跨度归 0、超 5min 的休眠/合盖伪影贡献 0；返回里带 `capped_per_record_seconds`），回答「我在 X 上花了多久」 |
| `get_category_stats` | `hours_back=24(≤720), top_n=20(≤50)` | 按分类聚合活跃时长（同样经 `_clamped_dur_sql("r")` 封顶），区分 `_unclassified`（系统内部尚未分类的积压桶）与 `uncategorized`（分类器真正判为「未分类」的桶）两个桶，返回带 `categories_legend` |
| `search_summaries` | `grain, period, start_iso/end_iso, query, limit, device_id?` | 精确粒度查询重叠窗口；返回 pending/open/ready 和 `has_more`，按返回时间窗下钻 |
| `apply_label` | `record_id: str, category: str, note: str\|None` | **唯一写工具**：只改某条记录的 `category_final`（写一行 `feedback` 审计行，action=edit），绝不删改记录/截图；未知 record/category 返回 `error` dict 而不抛异常 |
| `ask_agent` | `question: str, hours_back=24(≤720)` | 服务端 Agent 工具循环（摘要优先 → 统计/检索 → 回答） |

近况、分页和摘要窗口的新契约返回带毫秒与时区偏移的 ISO 时间。时区来自请求 `X-Timezone`，否则使用服务端设置。分页/下钻原样回传时间字符串。

### 读取契约与兼容边界

- “刚刚做什么”使用 `get_latest_activity`；“今天做什么”使用 `get_activity_overview`；逐条回放才使用 `get_recent_activity`。工具文档、Web Agent prompt 与仓库分发的 Skill 同步此路由。
- 分页默认 ASC 保持旧行为，新增 `order=desc`。`next_cursor` 变为不透明选择器，固定时间窗、设备和方向；同毫秒记录用 ID 破同值，取 `limit+1` 精确判断末页。旧 record-ID cursor 仍接受，但只新游标有固定窗/入库高水位保证。高水位并非数据库事务快照：已有记录的分析可更新，删除/VACUUM 等维护后应重新开始查询。
- `get_activity_overview` 的 today/week/month 从请求时区自然日零点起，custom 使用半开区间，最大31天。存储金字塔的服务端时区和4点切日不变。旧统计和精确摘要的逻辑日语义保持兼容；跨入口对齐时回传明确起止。
- 总览按设备取已闭合、已生成的最粗可用窗口；待生成/缺失/越界父窗回退到细窗，不重叠重复计入。**不裁剪整段叙述冒充子区间内容**。缺失设备历史摘要时报告 unavailable，不混入全设备旧摘要。
- `coverage` 是实时记录落在返回窗口内的数量，不是叙述语义准确度或迟到数据已被重新总结的保证；`uncovered_ranges` 不是无活动时段。`limit` 按设备计算，`truncated=true` 时缩小范围。
- 精确 `search_summaries` 不做自动回退；它显式报告 `availability`、`window_closed`、`has_more`，查询词过滤后的无结果不能证明没有活动。
- 最新记录包含 `server_received_at`（该记录首次入库）和设备 `last_contact_at`（服务端观察到的联系）；迟到上传不能冒充最新采集。空描述保留原始分析状态，所有设备的 `presence` 都是 unknown。

### 关键词检索的两档策略（`search_activity` / DB 层）

`search_activity` 通过共享检索层融合关键词与语义。关键词分支按查询长度二选一（阈值 `len(keyword.strip()) >= 3`）：

- **≥ 3 字**：走 `records_fts`（FTS5 **trigram** 分词器）`MATCH` + **BM25** 排序。trigram 对中文友好——「鸣潮」这类多字关键词能正常命中。
- **< 3 字**：trigram 切不出三元组，无法 `MATCH`，回退到**多字段 `LIKE`**。

> 这就是为什么工具 docstring 强调「≥3 char 用 FTS5 trigram + BM25，更短的回退 LIKE」——1~2 字 CJK 查询拿不到 BM25 相关性排序，只能子串匹配。

### `ask_agent` 的设计取舍（依赖 `VLMConfig`）

`ask_agent` 是唯一依赖 LLM 的工具，依赖 `AppConfig.vlm`（`common/config.py::VLMConfig`，由 `TIMETRACE_VLM_*` env 构建）：

- **优雅降级**：`vlm_cfg is None`（未配 API Key）时返回「LLM endpoint not configured」，确定性读工具继续工作。
- **共享循环**：每次调用创建 `AgentRunner`，至多4轮工具调用，完成后关闭客户端连接；按问题选择近况、摘要、统计或检索，不再固定读取80条。
- **预算与证据**：摘要总览优先，确需细节再下钻。结果为空、分页未读完或摘要未生成时如实报告覆盖限制，不自动推断用户离线。

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

`FastMCP` 的四个非默认构造参数（`server.py::build_mcp_server`）：

| 参数 | 值 | 原因 |
|------|----|------|
| `stateless_http` | `True` | 每请求独立，无可恢复流；对「调一个工具拿一段 JSON 就完」的用法更简单 |
| `json_response` | `True` | 返回纯 JSON 而非 SSE 流，curl 可调、契合单发工具客户端 |
| `streamable_http_path` | `"/"` | FastMCP 默认是 `/mcp`，会和我们 FastAPI 的 `/mcp` mount 点叠加成 `/mcp/mcp/`；设为根，客户端直接连 `/mcp/` |
| `transport_security` | `TransportSecuritySettings(...)` | 不传此参数时，FastMCP 在 `127.0.0.1` 绑定下会隐式开 DNS-rebinding 保护、且只认 localhost 的 Host 头；nginx 反代后公网域名的 Host 进来会被拒、`timetrace.yukirin.me/mcp/` 返回 421。显式给 allowlist 放行 `timetrace.yukirin.me` + `localhost`/`127.0.0.1`/`[::1]`，保护仍开，只是把公网域名重新放进白名单 |

---

## 鉴权：为什么 `/mcp` 是 bearer-only

登录系统（cookie session）是给浏览器 Web UI 用的；MCP 客户端（Claude Code / Desktop）是程序化调用，**只走 bearer token**（`server/auth.py::ServerAuth`，`tt_live_` 前缀）。换言之：

- `/mcp` 与 `/v1/ingest/*` 同属「机器对机器」面，用 bearer token 鉴权，不接 cookie 登录态。具体实现：mount 前用 `BearerOnlyMiddleware(mcp_app, auth)` 把整个 MCP 子应用裹一层 ASGI 中间件，统一校验 `Authorization: Bearer tt_live_...`。
- `/healthz` 是公开探活；浏览器业务路由走 cookie/bearer principal，MCP 与 ingest 只接受 bearer。
- server 默认监听 loopback；公网 MCP 经受控反向代理到同一进程，并继续受 bearer 与 Host allowlist 双重保护。

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
| **不返回原始截图** | 工具只返回文本描述 / 元数据 / 统计 / 自然语言回答，绝不含图片 URL 或图片 base64 |
| **数量上限** | 每个工具都对 `limit` / `top_n` / `hours_back` 做 `min(max(...))` 夹取，防止一次塞给模型海量帧 |
| **loopback 服务** | API 默认 `127.0.0.1`；外部访问经受控入口与鉴权 |
| **bearer 鉴权** | 机器面统一 bearer token，不复用浏览器 cookie 登录态 |

---

## tools.py 的历史包袱

`server/mcp_layer/tools.py` 里还留着一组早期（Phase 1.5）独立的 async 工具函数：`list_categories` / `get_activity` / `get_category_stats` / `search_activity`。其中 `get_category_stats` 与 `search_activity` 仍是**返回桩数据**的占位实现，且 `get_category_stats` / `list_categories` 依赖一张 `categories` 表。

**这组函数已不是当前 MCP 的对外接口**——真正暴露给 agent 的是 `server.py::build_mcp_server` 中注册的工具。旧 tools.py 视为待清理的遗留模块，新功能加到共享 Agent 实现，再从 MCP 注册。

---

## MCP 规范参考

MCP（Model Context Protocol）是开放协议，用于把外部数据源与工具接入 LLM 应用生态。TimeTrace 通过 `FastMCP` 实现 streamable HTTP 传输，按规范定义 tools，作为外部智能体读取桌面活动记忆的统一入口。

---

## 相关文档

- [Local API Server（MCP 复用同一服务层）](api-server.md)
- [Analysis Worker（VLM 描述写回，喂给 ask_agent 的素材）](analysis-worker.md)
- [架构总览](overview.md)
