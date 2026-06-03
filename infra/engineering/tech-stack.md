# 技术栈与依赖

> 本文档说明 TimeTrace 的技术选型、第三方依赖、为什么选它们，以及当前实装状态。
> 面向想读懂当前架构的工程师。深度数据流 / Schema 见 [infra/readme.md](../readme.md)。

## 语言与运行时

- **Python 3.12+**：大量使用 `asyncio`、`match` 语句、新式类型注解（PEP 604 `X | None`）
- **uv**：依赖管理与虚拟环境（替代 pip + venv）。`uv sync` 装运行依赖，`uv run <script>` 跑入口
- **Windows Only（主进程）**：采集层依赖 pywin32，`sys_platform == 'win32'` marker 隔离。服务端（`timetrace-server` / `timetrace-embserver`）是纯跨平台的，部署在 Linux 小主机上

## 三层重组

代码已按 `src/timetrace/{common,client,server,embserver}/` 四层组织：

- **common**：配置（`common/config.py`）、wire 协议（`common/protocol.py`）等 client/server 共享物
- **client**：采集、托盘、outbox、backend、init 命令
- **server**：API、DB、queue、blob、worker、vlm、phash、mcp、auth、admin 命令、bootstrap，以及 rules（分类引擎）/ agent（MCP 工具实现）/ settings（per-app 覆盖）/ report（看板）/ users（登录）
- **embserver**：独立的本地 embedding 推理服务（Qwen3-VL），自带 torch optional extra

运行时仍有两种入口拓扑（单进程 `timetrace` vs 双进程 `timetrace-server` + `timetrace-client`），详见 [packaging.md](../architecture/packaging.md) 与 CLAUDE.md 的「运行模式」表。

## 后端核心依赖

声明在 `pyproject.toml` 的 `[project].dependencies`，`uv sync` 默认全装。

| 库 | 用途 | 状态 |
|----|------|------|
| FastAPI | HTTP API 框架（server + embserver 都用） | ✅ 已用 |
| uvicorn[standard] | ASGI 服务器 | ✅ 已用 |
| aiosqlite | 异步 SQLite | ✅ 已用 |
| structlog | 结构化日志（全局禁用 `print`） | ✅ 已用 |
| mss | 屏幕截图 | ✅ 已用 |
| pillow | 图像处理（缩略图、pHash 输入） | ✅ 已用 |
| pywin32 | Windows API（窗口/进程信息），`win32` marker | ✅ 已用 |
| openai | VLM / embedding / ask_agent 的 OpenAI 兼容 chat+embeddings 客户端（`AsyncOpenAI`） | ✅ 已用 |
| httpx | 异步 HTTP 客户端（HttpBackend，client 侧） | ✅ 已用 |
| numpy | 向量运算（余弦相似度全表扫、packed BLOB 解码、手写 pHash DCT） | ✅ 已用 |
| python-dotenv | `.env` 加载（main.py 在 `AppConfig()` 前调 `load_dotenv()`） | ✅ 已用 |
| mcp / fastmcp | MCP 协议导出（streamable HTTP 挂 `/mcp`） | ✅ 已用 |
| bcrypt | 登录密码哈希（auth_users） | ✅ 已用 |

## 数据存储

- **SQLite（WAL 模式）**：单文件数据库，`server/db/sqlite.py::SqliteDatabase`。`server/db/__init__.py` 导出 `Database = SqliteDatabase` 别名（P5 引入 PostgresDatabase 时这条别名升级为 `typing.Protocol`）
- **本地文件系统**：截图 / 缩略图 PNG 存 `%USERPROFILE%/TimeTraceData/`（不在仓库内）
- **表**：`records / screenshots / analysis_results / records_fts`（FTS5 trigram）+ 登录用 `auth_users / auth_sessions` + `categories / tags / record_tags / feedback / settings / reports`（worker 任务队列复用 `analysis_results.status` 状态机，无独立任务表）。此处只列核心表，完整 schema 见 `server/db/sqlite.py` / [infra/readme.md](../readme.md)
  - `analysis_results` 含 `text_embedding`（packed float32 BLOB）+ `text_embedding_model`
  - 幂等索引：`records.client_record_id` UNIQUE（record 级）+ `screenshots(record_id, hash_sha256)` UNIQUE（防 outbox at-least-once replay 双插）

## VLM 接入（已实装，非桩，默认本地 LM Studio）

- **客户端**：`server/vlm/client.py`。通过 `.env`（`TIMETRACE_VLM_BASE_URL` / `_API_KEY` / `_MODEL` / `_DISABLE_THINKING`）配置，默认指向本地 **LM Studio 跑的 Qwen3-VL**
- **结构化输出**：`response_format` 用 `json_schema`（非 `json_object`），强约束模型输出 schema
- **思维链兜底**：`content` 为空时 fallback 读 `reasoning_content`
- **优雅降级**：没设 API Key → `VLMConfig.from_env()` 返回 `None` → worker 跳过 `_describe`，搜索的 semantic 通道返回 `unavailable`，**不报错**
- **生命周期**：退出前必须 `await vlm_client.aclose()`，否则 httpx 连接池在 atexit 报警

## 文本 Embedding（已实装）

- **客户端**：`server/embedding/client.py::EmbeddingClient`，走 OpenAI 兼容 `/v1/embeddings`（基于 `AsyncOpenAI`）。可连本地 embserver、LM Studio、vLLM、Ollama 或任何兼容端点
- **向量格式**：packed float32 BLOB（`4 × dim` 字节）存 `analysis_results.text_embedding`，搜索侧用 `np.frombuffer(blob, dtype=np.float32)` 读回；`analysis_results.text_embedding_model` 记录所用模型
- **配置**：`common/config.py::EmbeddingConfig`（`TIMETRACE_EMBEDDING_*` env；缺 model 则 disable，优雅降级）
- **写入时机**：worker（`server/worker/loop.py`）在 `vlm_done` 后 `_embed_and_save`（best-effort）+ 启动时 `_backfill_embeddings` 一次性回填（双失败模式：单行 poison skip / 连续失败 abort）

## Embedding Server（embserver，本地 Qwen3-VL，端口 8766）

独立服务 `src/timetrace/embserver/`，把多模态 embedding 从主 server 解耦出来，跑自己的 torch 进程（默认模型 **Qwen3-VL-Embedding-2B**，端口 8766）。`_vendored/qwen3_vl_embedding.py` 是官方 embedder 的随仓 vendored 副本（Apache-2.0，见 `_vendored/LICENSE`）。

- **接口**：FastAPI（`embserver/api.py`），`POST /v1/embeddings` 对齐 SiliconFlow `EmbeddingsVLRequest`，`input` 吃字符串 / text 对象 / image（url 或 base64）对象 / 混合数组；响应为标准 OpenAI embeddings envelope
- **鉴权**：Bearer key，`tt_emb_` 前缀；`EmbServerConfig.ensure_api_key()` 首启自动生成并 log 一次，建议用 `TIMETRACE_EMBSERVER_API_KEY` pin 住（否则控制子命令无法鉴权）
- **并发**：engine 串行（单 asyncio 锁），避免多请求争抢单卡显存
- **资源管理**：JIT load（首个请求时加载）+ idle TTL 自动 unload 释放显存（`_TTL_SWEEP_SECONDS=15` 巡检；config 默认 `idle_ttl_seconds=0` 常驻，systemd unit 覆盖为 900）+ dtype 热切换（默认 `bfloat16`，近无损 ~4.5GB on 2B）。控制类子命令（`status/load/unload/ttl/selftest`）是对运行中 daemon 的 `/admin/` HTTP 瘦客户端，仿 `lms` 驱动 LM Studio
- **配置**：`embserver/config.py::EmbServerConfig`，`TIMETRACE_EMBSERVER_*` 前缀，刻意与 `common.config.AppConfig` 解耦（自己进程、自己端口、自己的重依赖）
- **依赖隔离**：`transformers / qwen-vl-utils / accelerate` 走 optional extra `embserver`；**torch / torchvision 刻意不在 extra 里**（须先从正确的 PyTorch index 装，避免误拉 +cpu build）。plain `uv sync` 不拉，deploy.sh 也跳过（见 packaging.md）
- **selftest**：`POST /admin/selftest` 对随仓 fp32 golden master 做漂移检查；量化阶梯对比见 `tools/embedding_check/RESULTS.md`（fp16 / bf16 / int8 / int4）

## 检索（retrieval）

三路召回 + RRF 融合：

- **FTS5 BM25**（关键词，已 shipped）：trigram tokenizer，CTE 实现。≥3 字走 `MATCH`，<3 字多字段 `LIKE` 回退
- **pHash BK-tree**（以图搜图）：内存 BK-tree，`PHashIndex.from_db` 启动时从 DB 重建
- **向量余弦**（语义）：`db.vector_search`（numpy 余弦全表扫），**已实装但 search 路由暂未调用即休眠**
- **融合**：`server/retrieval.py::reciprocal_rank_fusion`（`k=60`，支持可选权重）

## 鉴权（auth，双通道）

`server/auth.py`：

- **Bearer token**（`tt_live_` 前缀，`ServerAuth`）：给 `/v1/ingest/*` 用；`load_or_generate()` 读 `~/.config/timetrace-server/tokens.json` 或首启自动生成
- **登录 session**（cookie）：bcrypt 密码 + `auth_users / auth_sessions`，HttpOnly cookie session，`admin/admin` 首启强制改密 + 登录限速
- **`require_principal`**：cookie 或 bearer 双通道任一通过即放行业务路由
- **不要 token**：`/healthz` 探活路由

## Outbox 可靠性（双进程 client 端）

`client/core/outbox.py`：append-only `log.jsonl` + `blobs/` + `state.json`（atomic rename + fsync）。`OutboxSender` 严格 FIFO + 指数 backoff + 可选 token bucket 限速 + `compact_every_n_acks=200` inline compaction（crash-safe 顺序保证最坏 at-least-once replay，配合上游 UNIQUE 索引去重）。

## MCP 导出

`server/mcp_layer/server.py::build_mcp_server`，FastMCP stateless_http 挂 `/mcp`（`streamable_http_path` 设为根，避免 `/mcp/mcp/` 双前缀），`session_manager.run` 在 lifespan 内。6 工具：`search_activity / get_recent_activity / get_app_breakdown / get_category_stats / apply_label / ask_agent`，全部 close over 并委托 `server/agent/tools.py` 的真实现。`mcp_layer/tools.py` 是已废弃的死 stub，不再被 `server.py` 引用（现走 `server/agent/tools.py`）。

## 前端

前端由 infra agent 单独维护，此处略。关键契约：前端**不被** Python 后端托管（独立 vite dev server + 代理），后端只 mount `/thumbs/` StaticFiles。

## 路线图（尚未实装）

- PostgreSQL 后端（多端部署，P5；届时 `Database` 别名升级为 Protocol）
- 向量检索接入 search 路由（当前 `vector_search` 已实装但休眠）

---

*最后更新：见 git log*
