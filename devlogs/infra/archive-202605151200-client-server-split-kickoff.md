# Client-Server 架构分离改造 — Kickoff

**日期：** 2026-05-15
**目标：** 把当前单进程的 TimeTrace 重构为 **客户端 / 服务端分离** 的架构。本文档是整个重构的"宪法"——后续每一个 PR 都应该能追溯到这里描述的某一阶段（P0–P8）。

---

## 起因

当前架构是单进程：capture / worker / api / DB / VLM 全在一个 Python 进程里，由 `asyncio.TaskGroup` 协调。对单机单用户是合适的，但限制了三类未来能力：

1. **多设备采集**：想在 Windows 桌面、小主机（headless）、Mac 同时跑采集，归到同一个时间线
2. **集中重计算**：VLM key 现在每个客户端各配一份；Postgres / Redis / S3 这些"基础设施级"组件天然属于"机房"，不该出现在每个客户端
3. **远程使用**：人在外面拿笔记本，看自己家里小主机上的活动归档

附加目标：把项目做得**开源友好**——其他人 clone 之后简单配置就能跑起来，欢迎贡献。

---

## 决策汇总（重构期不可动摇）

这些是经过多轮对齐后定下的，重构期间**不再讨论**，遇到冲突以这里为准。

| 维度 | 决定 |
|---|---|
| 边界切法 | "智能服务端"（Cut B）——客户端只采集 + 隐私过滤 + 上行 |
| 启动形态 | **双进程默认 loopback**，没有 embedded 模式，没有 in-process backend |
| 仓库结构 | **单包 + 子目录**：`src/timetrace/{client,server,common}/`，单 pyproject.toml，extras 拆依赖 |
| 服务端默认实现 | SQLite + asyncio.Queue + 本地文件；Postgres / Redis / S3 为可选适配器 |
| Wire 协议 | HTTP + multipart（不上 gRPC、不 base64 塞 JSON） |
| 鉴权 | Bearer token；服务端首启自动生成；客户端 `init` 交互填入 |
| 设备模型 | `device_id`（UUID）+ `device_name` + `device_desc`；token 与 device_id 解耦 |
| 隐私 | 本地 OCR + 框定位 + 小模型过滤 + 模糊；**不留原图、不传文字**，只传模糊后的图 |
| 上行 | 落盘 outbox（append-only），断网重试，可配置限速 |
| 客户端形态 | Windows 桌面 + headless TUI 两种 entry，共享 `client/core/` |
| LICENSE | MIT |
| 重构流 | `feature/refactor-split` 分支，P0–P8 各一个 PR 合回分支，最终一次性合 main |
| 现有数据 | `~/TimeTraceData/` 已重命名为 `~/TimeTraceData-archive-2026-05-15`，用户自行清理 |

---

## 架构概览

```
┌────────────────────────────────────────────────┐
│  CLIENT  (Windows desktop / headless TUI)      │
│                                                 │
│  Capturer ──► PrivacyFilter ──► Outbox          │
│   (window/    (OCR + detect    (append-only     │
│    process)    + blur)          file + offset)  │
│                                       │         │
│                                       ▼         │
│                              HttpBackend ──┐    │
│                              (throttle,    │    │
│                               retry)       │    │
└───────────────────────────────────────────│────┘
                                            │ HTTP multipart
                                            │ Bearer auth
                                            ▼
┌────────────────────────────────────────────────┐
│  SERVER                                         │
│                                                 │
│  Ingest API ──► Queue ──► Worker ──► VLM        │
│       │            │         │                  │
│       ▼            │         ▼                  │
│  BlobStorage       │     analysis_results       │
│       │            ▼                            │
│       └──► Database (records, FTS5, pHash)      │
│                                                 │
│  Search API / MCP / Frontend ───◄ Database     │
└────────────────────────────────────────────────┘
```

### 客户端职责

- **Capturer**：Windows 活跃窗口 / 截图；headless 变体为 `ProcessCapturer`（psutil）
- **PrivacyFilter**：本地 OCR 定位文字框 → 小模型判断敏感 → 强模糊。**原图绝不落盘、绝不上传**；OCR 抽出的文字**不上传**
- **Outbox**：append-only 文件 + offset 索引，断网时本地堆积，恢复后按速率限制重发
- **HttpBackend**：所有对服务端的调用都走它；唯一的 BackendClient 实现
- **Config**：`%USERPROFILE%/TimeTraceData/client.toml`（含 server URL、auth token、device_id、device profile、隐私策略、限速）

### 服务端职责

- **Ingest API**：接收 client 上行（record + 模糊 thumb + MD5）；幂等
- **Queue**：默认 asyncio.Queue；可换 Redis
- **Worker**：消费队列，调 VLM，回写分析结果
- **VLM Client**：唯一持有 API key 的地方
- **Database**：默认 SQLite + FTS5；可换 Postgres
- **BlobStorage**：默认本地文件；可换 S3 / dual-write
- **pHash Index**：BK-tree，从 DB 重建
- **Search API / MCP / Frontend**：维持现有对外契约

### 共享 (`common/`)

- `Record` 数据类
- Wire 协议 schema（pydantic）
- 配置基类
- 日志 setup
- 时间 / 路径工具

---

## 关键设计细节

### 1. 仓库布局

```
TimeTrace/
├── pyproject.toml              # 单包，extras 拆依赖
├── README.md                   # 用户向：装哪个、怎么跑
├── LICENSE                     # MIT
├── docker-compose.yml          # 一键起 server（可选 postgres/redis）
├── Dockerfile                  # server 镜像
├── .env.example                # 服务端环境变量模板
├── scripts/
│   └── dev.ps1                 # 同窗口起 client+server（开发用）
├── src/timetrace/
│   ├── common/                 # 协议、配置、日志、共享类型
│   ├── client/
│   │   ├── __main__.py         # entry: timetrace-client
│   │   ├── tui/                # entry: timetrace-headless
│   │   ├── core/               # 共享：outbox、backend、config
│   │   ├── capture/            # Windows desktop 采集
│   │   ├── privacy/            # OCR + detect + filter + blur
│   │   └── backend/            # HttpBackend
│   └── server/
│       ├── __main__.py         # entry: timetrace-server
│       ├── api/                # FastAPI 路由（ingest / search / mcp）
│       ├── worker/             # VLM 消费者
│       ├── db/                 # Database Protocol + sqlite/postgres
│       ├── queue/              # Queue Protocol + memory/redis
│       ├── storage/            # BlobStorage Protocol + local/s3
│       ├── search/             # pHash index、FTS5 查询
│       ├── vlm/                # VLM 客户端
│       └── mcp/                # MCP 工具
├── frontend/                   # React，独立 dev server
├── tests/
│   ├── common/
│   ├── client/
│   └── server/
├── infra/                      # 架构 wiki（重构期间持续迁移）
└── devlogs/
```

依赖按 extras 拆：

```toml
[project.optional-dependencies]
client      = ["mss", "pywin32", "Pillow", "httpx", "pydantic", ...]
client-priv = ["paddleocr", "onnxruntime", ...]   # 隐私管线模型
server      = ["fastapi", "uvicorn", "aiosqlite", "structlog", ...]
server-pg   = ["asyncpg"]
server-redis= ["redis"]
server-s3   = ["boto3"]
headless    = ["psutil"]                          # TUI 客户端
all         = [...]                               # 单机全装
dev         = ["pytest", "pytest-asyncio", "ruff", ...]
```

### 2. Wire 协议

**Ingest 上行**（最重要）：

```
POST /v1/ingest/record
Authorization: Bearer <token>
X-Device-Id: <uuid>
Content-Type: multipart/form-data

parts:
  record: JSON {
    client_record_id: str,    # client 生成，幂等键
    ts_start: int,            # client 时间，server 不矫正
    ts_end: int | null,
    app_name: str,
    window_title: str,
    url: str | null,
    interaction: { keys: int, clicks: int, ... },
    thumb_md5: str,           # 客户端算的，server 校验
    thumb_format: "webp",
    privacy_applied: bool,    # 是否走过隐私管线
    schema_version: int
  }
  thumb: bytes (image/webp)

response 200:
  { record_id: str, server_received_at: int }

response 409 (duplicate by client_record_id):
  { record_id: str, server_received_at: int }   # 幂等返回相同
```

**辅助端点**：

```
POST /v1/ingest/heartbeat    # client 心跳，server 知道哪些设备在线
GET  /v1/sync/missing        # client 启动时问：上次的 client_record_id X 之后哪些没收到
GET  /v1/config/privacy      # 拉取服务端下发的隐私规则版本（非强制）
POST /v1/auth/whoami         # 调试用：校验 token + 返回 token label
```

**搜索 / MCP / Frontend**：维持现有 `/v1/search/*`、`/v1/records/*`、`/v1/thumbs/*` 契约，只是现在它们的"客户端"是浏览器和 MCP，不是采集器。

### 3. 鉴权 + 初始化

**服务端首启**：

```
$ uv run timetrace-server
[INFO] No tokens configured. Generated a new one:
[INFO]   token = tt_live_K4mzN7pQ...vR
[INFO]   label = default
[INFO] Saved to ~/.config/timetrace-server/tokens.toml
[INFO] Listening on http://0.0.0.0:8765
```

**客户端首启**：

```
$ uv run timetrace-client init
Server URL [http://127.0.0.1:8765]: ▏
Token: tt_live_K4mzN7pQ...vR
Device name [Yuki-Windows-Laptop]: ▏
Device description: 日常开发与写作
Privacy mode [full/text_only/off, default full]: ▏
Upload speed limit (KB/s, 0=unlimited) [0]: ▏

Generated device_id: 9f3a1b2c-...
Config saved to %USERPROFILE%/TimeTraceData/client.toml
Testing connection... OK
$ uv run timetrace-client
```

**`device_id` 持久化**：写进 `client.toml` 的 `[device]` 段；每次上传都带 `X-Device-Id` header。token 列表（多设备共用一个 token 或一对一都行）由用户决定。

### 4. 客户端 Outbox

`%USERPROFILE%/TimeTraceData/outbox/` 结构：

```
outbox/
├── 2026-05-15T10-23-44.jsonl   # 当天的 append-only 队列
├── blobs/
│   └── <client_record_id>.webp
└── state.db                     # SQLite: 已确认 offset、最后心跳
```

每个 record 写 jsonl 一行 + blob 写一个文件，原子（fsync 后才视为入 outbox）。后台 task 顺序读 jsonl，调 `HttpBackend.submit`，成功后更新 `state.db` 的 offset。失败按 backoff 重试。

**限速**：token bucket，配置 `client.upload.max_kbps`（0 = 不限）。

**回收**：超过 N 天的已确认 jsonl + blob 删除（client 不做长期存储）。

### 5. 客户端隐私管线（P4 详细设计，这里先列骨架）

```
screenshot bytes
    │
    ▼
OCR with text box detection
    │  (PaddleOCR det+rec / RapidOCR / 待定)
    ▼
List<TextBox> { bbox, text, conf }
    │
    ▼
Privacy classifier (small model)
    │  对每个文字框判断 sensitive?
    ▼
List<TextBox + sensitive_flag>
    │
    ▼
Strong gaussian blur on sensitive bboxes
    │
    ▼
Re-encode webp
    │
    ▼
Compute MD5 → outbox.append(record, blurred_webp)

原图: 永不落盘
OCR 文字: 仅用于内部判断，不上传也不落盘
```

模型选型留到 P4 决定。

### 6. 服务端可替换组件

每个组件先定 Protocol，默认实现 + 可选适配器。

| 接口 | 默认 | 可选 |
|---|---|---|
| `Database` | `SqliteDatabase` (aiosqlite) | `PostgresDatabase` (asyncpg) |
| `Queue` | `MemoryQueue` (asyncio.Queue) | `RedisQueue` |
| `BlobStorage` | `LocalBlobStorage` | `S3BlobStorage`、`DualBlobStorage`（local + s3） |

切换全部通过 `server.toml` / 环境变量。clone-and-run 默认全走第一栏。

### 7. Headless TUI 客户端

- 复用 `client/core/`（outbox、HttpBackend、config）
- 替换 `capture/` 为 `tui/process_capturer.py`：
  - psutil 抓 process list（按 CPU% / RSS 排序，取 top N）
  - 可配置白名单进程（"我只关心这几个长跑服务"）
  - 截图 = null；上行 record 是"窗口标题"位填进程名，"url"位填 cmdline
- entry `timetrace-headless`，可在 Linux 小主机上跑

---

## 分阶段实施

每个 P 是一个独立可 merge 的 PR，回到 `feature/refactor-split` 分支。CI 在每个 P 完成后绿。**main 在重构期间不动**，最后整个 feature 分支一次 merge。

### P0 — 工程基建

**目标**：在改架构之前先把开发流水线搭好。

- [x] GitHub Actions workflow：`ruff check` + `ruff format --check` + `pytest`（已存在，本次扩展触发分支到 `feature/refactor-split`）
- [x] `pre-commit-config.yaml`：ruff format hook（已存在）
- [x] `LICENSE` 文件（MIT）
- [x] README 加 "Status: under refactor" banner
- [ ] 开 `feature/refactor-split` 分支

**Dockerfile / docker-compose 推迟到 P1 之后**（原 P0 列表里的两项）：当前代码依赖 `pywin32` 等 Windows-only 包，Linux 容器 `uv sync` 会失败。要让镜像真能 build，必须先有 P1 的依赖按平台拆开 + P2 的接口抽离。具体规划在 P5（"Server 可替换组件"）里一并落地。

**Exit criteria**：CI 在新分支触发、LICENSE 在仓库根、README banner 已上、`feature/refactor-split` 分支拉好。

### P1 — 目录重组（零行为变化）

**目标**：把现有 `src/timetrace/` 按目标骨架重新分布到 `client/` / `server/` / `common/`，**不改任何业务逻辑**。

- [x] 新建子包目录（`common/` `client/` `server/` + 各自 `__init__.py`）
- [x] 按归属移动模块（用 `git mv` 保留 history）：
  - `capture/` → `client/capture/`，`tray.py` → `client/tray.py`
  - `api/`、`worker/`、`vlm/`、`storage/database.py`、`phash_index/{index,bk_tree}.py`、`mcp_layer/`、`rules/` → `server/...`
  - `config.py`、`storage/models.py`（CaptureContext）、`phash_index/hash.py`（pHash 纯算法） → `common/`
- [x] 修 import 路径（src + tests 共 ~67 处；脚本一次性 regex 重写后人工 grep 复核零残留）
- [x] 所有测试照常跑过（`uv run pytest` 101 passed）
- [ ] ~~`pyproject.toml` 的 `[project.scripts]` 加 `timetrace-client` / `timetrace-server`，原 `timetrace` 暂时指向 server entry（兼容）~~ **推迟到 P3a**：P1 没拆 entry，提前加只会得到两个跑 server 单进程的别名，对用户混乱、对调试无用

**Exit criteria**：✅ `uv run pytest` 全绿（101 passed in ~6s），✅ `uv run ruff check` + `ruff format --check` 全绿，✅ `git status` 全部 `renamed:` 无 `deleted: + new file:` 丢史

**P1 已知债务（待 Phase 2/P2 主体清理）**：

1. `common/config.py:8` 仍 `from timetrace.server.vlm.client import VLMConfig`，构成 **common → server** 反向依赖。**紧接着下一个 commit（refactor(config)）抽 VLMConfig 到 common.config**，让 common tier 干净
2. `client/capture/service.py` 仍直接 import `server/storage/database` 与 `server/phash_index/index`，构成 **client → server** 跨层 import。**等 P2 主体的 BackendClient Protocol 落地**才能切，本期接受
3. `server/api/routes/feedback.py` 仍直接用 `async with db.lock: db.conn.execute(...)`（lock 字段暴露），P2 设计 Database Protocol 时一并封装
4. `src/timetrace/privacy/__init__.py` 空目录保留原位，归属待 P3 决定

### P2 — 抽接口

**目标**：把"组件间的调用"从直接引用改成走 Protocol，为 P3 切 HTTP 边界做准备。

- [x] 定义 `common/protocol.py`：`ScreenshotSubmission` dataclass（capture→backend 唯一非 CaptureContext 的传值类型；pydantic 留 P3a 上 HTTP 时一并加）
- [x] 定义 `client/core/backend.py::BackendClient` Protocol + `InProcessBackend` 直调实现
- [ ] 定义 `server/db/__init__.py::Database` Protocol（现有 `database.py` 改名为 `server/db/sqlite.py` 实现）—— **推迟到 P2.5**：本期 capture 已经只看 BackendClient，server 端 Database 暴露面收口可与 worker / api 改造一并做
- [ ] 定义 `server/queue/__init__.py::Queue` Protocol、`server/storage/__init__.py::BlobStorage` Protocol —— **推迟到 P2.5**：同上，与 ingest API 一起设计
- [x] capture 调用从直接 `database.insert_record(...)` 改成 `backend.submit_record(...)`；此时 BackendClient 还是 in-process 直调
- [x] 给 BackendClient 写一个 fake 测试 double（`tests/test_backend_inprocess.py` 7 个测试，覆盖 5 个 Protocol 方法 + phash 双写一致性）

**Exit criteria**：✅ pytest 108 passed（101 → 108），✅ ruff check + format --check 全绿，✅ capture/service.py 不再 import 任何 `timetrace.server.*`（client→server 跨层耦合的运行时路径已切除；剩余只有 main.py 装配处的 `InProcessBackend(db, phash_index)` 一行 — 这是单进程模式不可避免的胶水点，HttpBackend 上线后自动消失）。

**P2 已知债务（待 P2.5 / P3a 收）**：

1. `client/core/backend.py:InProcessBackend` 在 TYPE_CHECKING 块中仍 import `server/storage/database.Database` 与 `server/phash_index/index.PHashIndex`（仅类型注解）。这是 in-process 适配器的本质 — 它就是 client 端拿到的"server 直引用"。HttpBackend 上线后 InProcessBackend 仅用于测试或单进程模式
2. `server/api/routes/feedback.py` 仍直接用 `db.lock` / `db.conn.execute(...)`（绕过 Database 方法）—— 与 P2.5 的 Database Protocol 一并收
3. `server/worker/loop.py` 仍直接持有 `Database` 引用（不走 BackendClient，因为 worker 在 server 进程内跑，本来就不需要走客户端 backend）—— 不算债务，是 server tier 内部的合理直调

### P2.5 — Server-side Protocol 抽离 + feedback 债务收口

**目标**：把 P2 推迟的 server 端 Protocol 落地，腾出 `server/storage/` 给 BlobStorage 用，顺手收掉 feedback 路由直访 `db.lock` 的债务。

- [x] `Database.get_category_final(record_id)` + feedback 路由改用此方法（commit `349dff1`）
- [x] `server/storage/database.py` → `server/db/sqlite.py`；类 `Database` 改名 `SqliteDatabase`；`server/db/__init__.py` 导出 `Database = SqliteDatabase` 别名（commit `3aee2a4`）—— 别名在 PostgresDatabase 进 P5 时升级为 typing.Protocol
- [x] `server/queue/__init__.py::Queue` Protocol + `InMemoryQueue` 实现；`server/storage/blob.py::BlobStorage` Protocol + `LocalBlobStorage` 实现（commit `653efba`）—— 骨架先到位，本期不接 wire（worker 仍走 SqliteDatabase 的 analysis_results 表，capture 仍直接写文件）
- [x] `pyproject.toml` 加 `[project.optional-dependencies]` 占位桶（commit `dbf3d62`）—— P4/P5/P6 真接入时解开注释

**Exit criteria**：✅ pytest 128 passed（108 → 128），✅ ruff check + format --check 全绿，✅ `feedback.py` 不再触达 `db.lock` / `db.conn.execute`（路由层只剩"调有名字的方法"）。

### P3a — HTTP 边界 + Outbox（基础设施层完成；与 capture 的接线留给 P3a-5）

**目标**：把 BackendClient 的实现切到 HTTP，能真正两进程跑。

- [x] `server/api/routes/ingest.py`：`POST /v1/ingest/record`（multipart `record` JSON + 可选 `image` + `thumb`，MD5 校验，幂等 by `client_record_id`）+ `POST /v1/ingest/record/{id}/close`（commit `1d3532b` + `77b1ec4`）
- [x] `records.client_record_id` 列 + 部分唯一索引 + `Database.ingest_or_get_record` 幂等 upsert（commit `48048e2`）
- [x] `common/protocol.py` 加 pydantic `IngestRecordPayload` / `IngestRecordResponse` 作为 wire schema
- [x] `client/core/backend.py::HttpBackend` 实现：`submit_record` 本地生成 `client_record_id` POST record-only；`submit_screenshot` 复用同一 id POST record+image（server 看到 was_new=False 后挂截图）；`close_record` POST `.../close` 带客户端时钟；`mark_pending` no-op（ingest 路由本身已 mark_pending）（commit `77b1ec4`）
- [x] `client/core/outbox.py`：append-only `log.jsonl` + `blobs/` + `state.json`（acked offset，atomic rename）；严格 FIFO；崩溃恢复（commit `8477488`）
- [x] 限速 token bucket：`OutboxSender(max_kbps=N)`，N=0 zero-overhead；pre-emptive consume + 1s 初始 burst budget（commit `600df57`）
- [x] `OutboxSender`：drain 循环 + 指数 backoff + 严格 FIFO + asyncio.Event 关停（commit `b4e62bf`）
- [x] 客户端配置：`ClientConfig` + `client.toml` 5 段 schema + `tomllib` 读 + 手写 TOML 输出 + `ensure_device_id()` 注入 UUID（commit `13734ed`）
- [ ] **P3a-5b（剩余）**：拼装 `timetrace-client` 入口（读 client.toml → 起 Outbox → 起 HttpBackend → 起 `OutboxBackend` (BackendClient → outbox.append) → 起 OutboxSender → 起 CaptureService），同时 `timetrace-server` 入口（仅 api + worker，去掉 capture）
- [ ] 端到端测试：起 server fixture + client driver 跑完整一条上传链路 —— P3a-5b

**关键设计决定（实施期固化下来的）**：

- **HttpBackend 不接 Outbox 之前直发**：现有 9 个 E2E 测试 `tests/test_http_backend.py` 通过 `httpx.ASGITransport` 直接打 in-process FastAPI app，零 socket 零线程。Outbox 接入是 P3a-5 的事
- **截图重复挂载防护推迟**：ingest 路由抹掉 was_new 守卫后，replay+image 也会插一张新 screenshot 行；at-most-once 投递的兜底由 outbox 单 sender FIFO 保证（不是 server 端 dedup-by-hash）。如果将来需要 server 兜底，最低成本是给 `screenshots` 加 `(record_id, hash_sha256)` 唯一索引
- **HttpBackend.mark_pending no-op**：ingest 路由每次都 `mark_pending`（INSERT OR IGNORE 幂等），所以 capture 在 submit_screenshot 之后那一发 mark_pending 在 HTTP 模式下被吞掉。InProcessBackend 仍照常 mark_pending（capture 行为不变）
- **client_record_id 用 UUID 不是 hash**：纯客户端生成 + UNIQUE 索引兜底，避免"同一 ts_start 同一窗口"被误判幂等

**Exit criteria（部分达成）**：✅ ingest API + HttpBackend + Outbox 三件套都有测试覆盖，pytest 164 passed；❌ 双终端跑通待 P3a-5；❌ 离线 5 分钟再恢复待 OutboxSender 实装。

### P3a-cleanup — 接线前的预防性 bug fix（2026-05-15 review 后落地）

**目标**：第三方 review 发现 7 个 P3a-5b 接线后会立刻踩到的 bug，全部在接线前消除，避免集成时与接线问题搅在一起难定位。

- [x] `outbox` 三处 fsync 缺失 + jsonl 末尾 partial line 容错（commit `50043d7`）
- [x] `insert_record` IntegrityError 路径 heal UPDATE 留挂事务 → 独立 commit + 显式 rollback（commit `ba6f3ca`）
- [x] `screenshots(record_id, hash_sha256)` UNIQUE 索引 + `insert_screenshot` 返 `(id, was_new)`，路由按 was_new 决定 phash_index 是否双插（commit `c85ca9e`）
- [x] `/close` 路由对未知 ID 返 404；route 接受 client_record_id 兜底；response.ts_end 用真值（commit `1168971`，同时解决 #2 跨重启 silent fail）
- [x] HttpBackend 接受 capture 输出的相对路径（`data_dir` 解析 + 路径穿越守卫）（commit `2e71d2d`）
- [x] HttpBackend borrowed-client 模式补 `Authorization` 注入（与 device_id 对称的 `_extra_headers` 模式）（commit `9dbc7a4`）
- [x] 截图 blob 路径用 `record.ts_start` 算日期目录，不用上传时刻（commit `c9b567e`）

**测试增量**：194 → 210 passed（+16 个）。每个 fix 都先写红测试再绿，闭环。

**未触动的低风险（接线后再回顾）**：tokens.json chmod 600（Linux 部署再说）、ClientConfig TOML 无注释、Outbox compaction、capture/service.py 三处 close_record helper 抽取、token bucket retry 不重扣。

### P3a-5b — 接线（已完成 2026-05-15 ~ 16）

**目标**：把 OutboxBackend 这一最后一公里写完，加 timetrace-client / timetrace-server 双入口，跑通真实双进程链路。

- [x] `client/core/outbox_backend.py::OutboxBackend`：实现 BackendClient Protocol，把 submit_record/screenshot/close 写成 outbox.append 条目（payload 直接是 wire-ready JSON）
- [x] `HttpBackend.post_ingest` / `HttpBackend.post_close` 提为 public，给 OutboxSender 的 send 回调用
- [x] `make_http_sender(http_backend)` 适配函数：根据 entry.payload["kind"] 选择 endpoint
- [x] `client/cli.py::main` 入口：load ClientConfig → 起 Outbox → HttpBackend → OutboxBackend → OutboxSender → CaptureService → 托盘（commit `5d8db1c`）
- [x] `server/cli.py::main` 入口：仅 Database + PHashIndex + AnalysisWorker + uvicorn(create_app)，去掉 capture / 托盘（abe7d24 抽 server.bootstrap 后两侧共享装配）
- [x] `pyproject.toml [project.scripts]` 加 `timetrace-client` / `timetrace-server`，原 `timetrace` 保留指向 main.py（单进程兼容）
- [x] E2E smoke 测试：in-process 起两侧（commit `be54f52`）

### P3b — Auth + Init（已完成 2026-05-16）

**目标**：把鉴权和初始化补齐，达到"开源可用"门槛。

- [x] Server 首启自动生成 token → `tokens.json`（commit `67276a2`）
- [x] Server 上行端点加 bearer 校验（commit `67276a2`）
- [x] `tokens.json` 支持多 token + label
- [x] device_id 字段 + 自动生成（commit `13734ed`）
- [x] `timetrace-client init` 交互式命令 + `--non-interactive` env 驱动（commit `d176829`）
- [x] `timetrace-server tokens list/add/revoke` + `info` 子命令（commit `d176829`）
- [x] tokens.json POSIX chmod 600 落盘加固（commit `d176829`）
- [x] ClientConfig 吃 storage/capture/privacy 三段 + apply_env_overrides() 支持 9 个 TIMETRACE_* env vars（commit `d195202`）
- [x] README 端到端使用流程重写：单进程 + 双进程 + init 流程 + 部署小主机（commit `7c9f28b`）

**Exit criteria**：✅ 261 tests passed；✅ 双入口 + init/admin CLI + README 全部端到端可用。

### P3c — 部署设施（新增段，2026-05-16）

**目标**：让 "git push → 30s 后小主机跑上新版本" 成为常规操作；fork 安全。

- [x] `deploy/deploy.sh`：在小主机上跑的部署脚本，8 个 env 变量（TIMETRACE_USER 等）支持 fork 用户 export 覆盖（commit `1ce47eb`）
- [x] `deploy/timetrace-server.service`：systemd `--user` 单元模板，`%h` 展开 home，sandbox 收紧（PrivateTmp/NoNewPrivileges/ProtectSystem=strict + ReadWritePaths 白名单）
- [x] `.github/workflows/deploy.yml`：`workflow_dispatch` only；`if: github.repository == 'Vanilla-Yukirin/TimeTrace'` + GH secrets 不被 fork 继承双保险；ProxyJump runner → 公网云 → FRP → 小主机
- [x] 部署架构 + CI/CD 设计落档（[archive-202605161000-deployment-architecture.md](archive-202605161000-deployment-architecture.md) + [archive-202605161015-cicd-workflow.md](archive-202605161015-cicd-workflow.md)）

**待外部一次性配置**（部署前）：
- 本机 `ssh-copy-id tt-rb4g` + 生成 CI 专用 ed25519 keypair（不复用本机 key）
- GH 仓库 settings 配 6 个 deploy secrets

### P4 — 客户端隐私管线（部分完成；OCR 主体待选型）

**已落地**:
- [x] capture/service.py 三处 close_record 抽 `_safe_close_record(where=...)` 助手 + 修一致性 bug（idle_start 之前裸 await，失败炸 capture loop）（commit `4e5c589`）
- [x] outbox_backend `_read_path` 接受 kind 参数，traversal guard 报错对称（commit `4e5c589`）
- [x] window.py ctypes 加 ERROR_INSUFFICIENT_BUFFER 重试（1024 → 32k）（commit `4e5c589`）
- [x] tokens.json POSIX chmod 600（commit `d176829`）
- [x] **Outbox compaction**：每 200 acks inline 触发，crash-safe 顺序（state 先于 log，最坏 at-least-once replay）（commit `548b0de`）

**待选型 + 实装**（需用户决策）:
- [ ] 选型 OCR（PaddleOCR det+rec / RapidOCR / 其他）
- [ ] 选型隐私分类小模型（OpenAI 开源 / 自训）
- [ ] `client/privacy/` 实现：OCR → 分类 → 模糊 → 重编码
- [ ] 配置 `[privacy] mode = full | text_only | off`（schema 已落地在 PrivacyConfig.mode，待 pipeline 接入消费）
- [ ] 性能 budget：单张截图全管线 ≤ 500ms
- [ ] 模型文件下载脚本（不进 git）

**Exit criteria**：截图本地不留原图；模糊后图人眼可读非敏感内容；测试集（信用卡号 / 邮箱 / 密码框）覆盖。

### P5 — Server 可替换组件 + 容器化（部分完成；Pg/Redis/S3 适配器待真接）

**已落地**:
- [x] `Dockerfile`：multi-stage builder + slim runtime；非 root 用户 timetrace；/data + /tokens 双 bind-mount；HEALTHCHECK 用 stdlib urllib（commit `44d61b5`）
- [x] `docker-compose.yml`：单服务 + 127.0.0.1:8765 only loopback bind（公网走 Caddy 反代）+ env_file 可选 + P5 future profile 占位（commit `44d61b5`）
- [x] `.dockerignore`
- [x] `pyproject.toml`：Windows-only 依赖（pywin32/mss/pynput/pystray）加 `; sys_platform == 'win32'` 标记，Linux 上 `uv sync` 自动跳过；optional-dependencies 桶重命名 `all` → `all-extras` 以与未来真实 extras 区分（commit `44d61b5`）

**待真接**:
- [ ] `PostgresDatabase` 实现（asyncpg）+ schema 迁移脚本
- [ ] `RedisQueue` 实现
- [ ] `S3BlobStorage` + `DualBlobStorage`（本地 + S3）
- [ ] 配置驱动选择
- [ ] CI 增加 server 镜像 build 步骤

**Exit criteria**：在 docker-compose 切换 profile 重启就能换实现，数据语义一致；镜像在 CI 上能 build。

### P6 — Headless TUI 客户端

**目标**：在 Linux 小主机上能跑 process-only 采集。

- [ ] `client/tui/` 实现
- [ ] `ProcessCapturer`：psutil 抓 top N + 白名单
- [ ] entry `timetrace-headless`
- [ ] Linux 测试

**Exit criteria**：小主机 systemd 起服务，能稳定 24h 上行。

### P7 — 分发自动化

**目标**：releases、镜像、自动部署。

- [ ] ghcr 自动推 server 镜像
- [ ] Release CI：tag → 自动 build wheel + 镜像
- [ ] 用户的小主机 webhook 拉取 + 重启
- [ ] CHANGELOG 自动化

**Exit criteria**：tag v0.x.0 后 30 分钟内小主机自动跑上新版本。

---

## 文档同步策略

- **本 devlog 是宪法**：所有 PR 应能追溯到这里的某个 P
- **`infra/readme.md`** 加一段 "重构期间状态说明" banner，指向本 devlog；具体子文档（architecture/、storage/ 等）在对应 P 完成时同步更新（每 PR 改 1–2 个 infra 文件）
- **v1 的架构现状**：不另写 snapshot 文件，git history 即权威；commit hash `dd1969b` 前的所有内容是 v1 现状
- **每 P 完成时**：在本 devlog 末尾追加 "P? 完成日志" 链接到对应 PR
- **CLAUDE.md**：每 P 完成时同步修订"项目简介"、"架构关键点"、"项目约定"几段

---

## 备份处理记录

- `~/TimeTraceData/` 已重命名为 `~/TimeTraceData-archive-2026-05-15`（21G，含 25M db + 21G screenshots + 204M thumbs）
- 截图为已压缩格式，再 zip 收益小，因此未压缩
- 重构后启动会重新创建空的 `~/TimeTraceData/`
- 清理由用户自行处理

---

## 待解决（不阻塞 P0/P1，到对应 P 再决定）

- **OCR 模型选型**：PaddleOCR det+rec？RapidOCR？deferred to P4
- **隐私分类小模型**：OpenAI 开源的那个 300MB 模型只能过滤纯文本，需要找对应的视觉/文本组合方案。deferred to P4
- **公网 TLS 方案**：推荐 Caddy / Cloudflare Tunnel 让用户在 Python 外面挂；文档示例放 P3b 后
- **TUI 采集范围**：只 process list 还是要扩展到 SSH session / Docker / log 等？deferred to P7
- **多用户**：当前所有设计假设单用户多设备。多用户租户模型留到 P5+ 再说

---

## 不在本次重构范围（明确排除）

- 双侧对等同步（之前讨论过 Cut C，排除）
- 客户端缓存查询结果（client 是纯生产者，不消费搜索结果）
- 前端集成进 server 进程（前端继续独立 vite 服务，build 产物可由 server StaticFiles mount，也可由 nginx 单独 serve）
- 现有 SQLite 数据迁移（用户已自行处理）
