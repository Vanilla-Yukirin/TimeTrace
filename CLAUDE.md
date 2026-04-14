# CLAUDE.md — TimeTrace 项目约定

## 项目简介

TimeTrace 是一个 Windows Only、本地优先的桌面活动记忆层（Python 3.12+，asyncio，FastAPI，SQLite）。

## 开发命令

```bash
uv sync                    # 安装/同步依赖
uv run timetrace           # 启动应用
uv run pytest              # 运行测试
uv run ruff check src/     # Lint
uv run ruff format src/    # 格式化
```

## 项目约定

- **入口**：`src/timetrace/main.py` — `asyncio.TaskGroup` 并发运行 capture / worker / api 三个任务
- **配置**：`src/timetrace/config.py` — 全部用 dataclass，不用环境变量
- **异步**：所有 DB 操作通过 `aiosqlite`，保持 async/await 风格
- **日志**：统一使用 `structlog.get_logger(__name__)`，不用 `print`
- **数据目录**：`%USERPROFILE%/TimeTraceData/`（不放在项目目录内）

## 架构文档

详细设计文档见 [infra/readme.md](infra/readme.md)。

## 桩代码说明

以下方法是有意保留的桩代码，将在对应 Phase 实现：

| 文件 | 方法 | 计划 Phase |
|------|------|-----------|
| `worker/loop.py` | `_describe()` | Phase 1.5（VLM） |
| `mcp_layer/tools.py` | `get_category_stats()`, `search_activity()` | Phase 1.5 |

## 测试

- `tests/` 下按模块划分：`test_storage`, `test_api`, `test_privacy`, `test_rules`
- 使用 `pytest-asyncio`，`asyncio_mode = "auto"`
- 不使用 mock DB；测试用内存 SQLite（`:memory:`）
