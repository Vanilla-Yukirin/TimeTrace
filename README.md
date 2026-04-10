# TimeTrace

**Windows Only 本地优先的桌面活动记忆层。** 低打扰地采集活跃窗口与关键帧截图，存储到 SQLite + 本地文件系统，支持时间轴回放、搜索与导出。第二阶段引入 VLM/Embedding 智能分析，通过 Local API + MCP 协议对外暴露结构化上下文。

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

## 项目文档

完整架构文档、模块设计、数据库 Schema、工程路线图见 **[infra/readme.md](infra/readme.md)**。

---

## 许可证

MIT
