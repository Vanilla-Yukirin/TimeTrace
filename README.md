# TimeTrace

**Windows Only 本地优先的桌面活动记忆层。** 低打扰地采集活跃窗口与关键帧截图，存储到 SQLite + 本地文件系统，支持时间轴回放、搜索与导出。第二阶段引入 VLM/Embedding 智能分析，通过 Local API + MCP 协议对外暴露结构化上下文。

> **⚠️ 正在进行架构级重构（2026-05-15 起）**
>
> 项目正在从单进程拆为 **客户端 / 服务端分离** 架构。重构在 `feature/refactor-split` 分支推进，`main` 分支当前仍是 v1 单进程版本，可以正常使用。
>
> 完整方案见 [devlogs/infra/archive-202605151200-client-server-split-kickoff.md](devlogs/infra/archive-202605151200-client-server-split-kickoff.md)。

---

## 快速启动

**要求**：Python 3.12+，[uv](https://github.com/astral-sh/uv)

```bash
# 安装依赖
uv sync

# 运行
uv run timetrace
```

API 启动后访问 `http://127.0.0.1:8765/docs` 查看 OpenAPI 文档。

---

## 开发

```bash
# 安装开发依赖
uv sync

# 运行测试
uv run pytest

# Lint
uv run ruff check src/

# 格式化
uv run ruff format src/
```

---

## 当前架构（`feature/refactor-split` 进度）

代码已经按 **client / server / common** 三层重组完毕（commit 历史从 `8aa6e2f` 开始可追）。本节是这条分支当前状态的速览，作用是让新加入的人或者从 `main` 切过来的开发者快速建立心智模型。`main` 上的描述仍然是 v1 单进程版。

```
src/timetrace/
├── common/          # 跨层共享：config、CaptureContext、phash、wire schema
├── client/
│   ├── capture/     # 切窗监听、截图、隐私门控、空闲检测
│   ├── core/        # BackendClient Protocol + InProcessBackend / HttpBackend / Outbox
│   └── tray.py      # pystray 托盘
├── server/
│   ├── api/         # FastAPI app + routes (records / search / feedback / ingest)
│   ├── auth.py      # bearer token 自生成 + 校验
│   ├── db/          # SqliteDatabase（PostgresDatabase 留 P5）
│   ├── queue/       # InMemoryQueue（RedisQueue 留 P5）
│   ├── storage/     # LocalBlobStorage（S3 留 P5）
│   ├── phash_index/ # BK-tree 内存索引
│   ├── vlm/         # OpenAI 兼容 VLM 客户端
│   ├── worker/      # 分析 Worker（消费 analysis_results 队列）
│   ├── rules/       # 规则 / KNN 投票
│   └── mcp_layer/   # MCP 工具
└── main.py          # 单进程入口；同时拉起 capture + api + worker
```

### 已经能跑的

- 单进程默认入口 `uv run timetrace` 不变（capture 走 `InProcessBackend` 直调 `SqliteDatabase`，与 v1 行为一致）
- 服务端 `POST /v1/ingest/record` 多通道 multipart 上行 + 幂等（按 `client_record_id`）+ MD5 校验 + BlobStorage 写入
- 客户端 `HttpBackend` 实现 `BackendClient` Protocol，能把 capture 的调用翻译成 ingest POSTs（含 `submit_screenshot` 双发模式 + `close_record`）
- 客户端 `Outbox`：append-only `log.jsonl` + `blobs/` + `state.json`，严格 FIFO，崩溃后重启保留 pending 与 acked offset
- `ServerAuth.load_or_generate()` 首启自动生成 `tt_live_<32urlbytes>` token 落地 `~/.config/timetrace-server/tokens.json`，ingest 路由强制 bearer
- 测试 176 passed，CI 在 `feature/refactor-split` 分支跟跑

### 还差什么（P3a-5 / P3b-2 / P3b-3）

- **OutboxSender 与 capture 接线**：现在 HttpBackend 不经 outbox，是同步 POST。capture 默认仍走 `InProcessBackend`。下个阶段写 `OutboxSender` 后台任务 + 把 close 也排队，再切 capture 走 outbox。
- **client.toml 配置 + `timetrace-client init` 交互式命令**：还没有 client 入口；当前所有客户端组件都从 `main.py` 单进程模式注入。
- **`timetrace-client` / `timetrace-server` 双入口**：pyproject 的 `[project.scripts]` 还只指向单进程 `timetrace`。

整体路线图与每阶段 exit criteria 见 [devlogs/infra/archive-202605151200-client-server-split-kickoff.md](devlogs/infra/archive-202605151200-client-server-split-kickoff.md)。

---

## 项目文档

完整架构文档、模块设计、数据库 Schema、工程路线图见 **[infra/readme.md](infra/readme.md)**。

---

## 许可证

MIT
