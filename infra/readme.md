# TimeTrace 项目 Wiki

> TimeTrace 是一个 **Windows Only、本地优先**的"桌面活动记忆层"。低打扰采集活跃窗口与关键帧截图，落地到 SQLite + 本地文件系统，支持时间轴回放、多模态搜索（关键词 + 以图搜图）与导出；第二阶段引入 VLM 结构化描述与 FTS5 BM25 打通语义检索；通过 Local API + MCP 协议对外暴露结构化上下文，默认不返回原始截图。

---

## **⚠️ 重构期间状态说明（2026-05-15 起）**

项目正在进行 **客户端 / 服务端分离** 的架构级重构。本 wiki 当前各页**仍描述 v1 单进程架构**（即 commit `dd1969b` 时的状态），与现状存在差距。

- **目标架构与分阶段方案**：见 [devlogs/infra/archive-202605151200-client-server-split-kickoff.md](../devlogs/infra/archive-202605151200-client-server-split-kickoff.md)（重构宪法，所有 PR 应能追溯到其中某个 P）
- **重构分支**：`feature/refactor-split`（main 在合并前不动）
- **文档迁移节奏**：每个阶段（P0–P7）完成时**只在受影响 wiki 子页顶部加 deprecation 警告**（保留 v1 内容作历史快照），整页重写延后到 P4 / P5 主体落地

### 目标架构一句话

- **客户端**（Windows 桌面 + headless TUI，共享 `client/core/`）：采集 → 本地隐私管线（OCR + 检测 + 模糊，原图绝不落盘）→ 落盘 outbox → HTTP multipart 上行
- **服务端**（默认 SQLite + 内存队列 + 本地文件；Postgres / Redis / S3 可选）：接收 → 入库 → Worker 调 VLM → 提供搜索 / MCP / Frontend
- **通信**：HTTP + Bearer token；客户端 `init` 命令交互式配置；服务端首启自动生成 token

### 已完成阶段速览（2026-05-17）

| 阶段 | 状态 | 核心交付 | 详见 |
|---|---|---|---|
| P0 工程基建 | ✅ | LICENSE / CI / 重构分支 | kickoff devlog |
| P1 目录重组 | ✅ | `src/timetrace/{common,client,server}/` 三层 | kickoff devlog |
| P2 / P2.5 接口抽象 | ✅ | BackendClient / Database / Queue / BlobStorage Protocol | kickoff devlog |
| P3a HTTP + Outbox | ✅ | ingest 路由幂等、HttpBackend、Outbox 严格 FIFO | kickoff devlog |
| P3a-5b 双入口接线 | ✅ | OutboxBackend + 三入口 + bootstrap 共享装配 | kickoff devlog |
| P3b Auth + Init | ✅ | tokens.json 体系 / chmod 600 / init/admin CLI | [p3b3-cli-design](../devlogs/infra/archive-202605171500-p3b3-cli-design.md) |
| P3c 部署设施 | ✅ | deploy.sh / systemd unit / GH Actions（workflow_dispatch） | [deployment-architecture](../devlogs/infra/archive-202605161000-deployment-architecture.md)、[cicd-workflow](../devlogs/infra/archive-202605161015-cicd-workflow.md) |
| P4 客户端隐私管线 | ⚠️ 仅周边硬化 | Outbox compaction / _safe_close_record / ctypes 长路径完成；OCR + 分类 + 模糊未启 | [outbox-compaction](../devlogs/infra/archive-202605171501-outbox-compaction-and-review-fixes.md) |
| P5 容器化 + 适配器 | ⚠️ 容器完成 | Dockerfile / docker-compose / pyproject 平台标记；PostgresDatabase / RedisQueue / S3BlobStorage 未启 | [packaging-and-container](../devlogs/infra/archive-202605171502-packaging-and-container.md) |
| P6 Headless TUI | ❌ 未启动 | — | — |
| P7 分发自动化 | ❌ 未启动 | — | — |

### 阅读 wiki 时

- **遇到与现状冲突的描述以代码 + 最新 devlog 为准**：每受影响子页顶部已加 H2 deprecation 警告，`grep '^## \*\*⚠️' infra/architecture/*.md` 一键定位过期最严重的页
- **子页内容是历史快照**：等 P4 / P5 主体落地再整页翻新；中间不做局部修补（局部修补会让 v1 描述与新增段落混杂，反而更难读）
- **完整状态优先看 [kickoff devlog](../devlogs/infra/archive-202605151200-client-server-split-kickoff.md)**：那是滚动更新的"重构宪法"

---

## 快速导航

### 三条入口路径

| 角色 | 从这里开始 |
|------|-----------|
| **新开发者** | [概览 → 执行摘要](overview/summary.md) → [架构总览](architecture/overview.md) → [开发路线图](overview/roadmap.md) |
| **存储 / 数据库** | [存储策略](storage/overview.md) → [数据库 Schema](storage/schema.md) → [相似检索层](storage/vector-search.md) |
| **AI 接入 / MCP** | [MCP Layer](architecture/mcp-layer.md) → [Local API Server](architecture/api-server.md) → [隐私策略](privacy/strategy.md) |

---

## 目录

### 概览

| 文件 | 内容 |
|------|------|
| [overview/summary.md](overview/summary.md) | 执行摘要、可量化验收标准、项目定位与非目标 |
| [overview/roadmap.md](overview/roadmap.md) | Phase 1 / 1.5 / 2 功能里程碑与目标 |

### 架构

| 文件 | 内容 |
|------|------|
| [architecture/overview.md](architecture/overview.md) | 分层原则、进程建议、端到端数据流（Mermaid 图） |
| [architecture/capture-service.md](architecture/capture-service.md) | 采集服务：切窗监听、截图策略、键鼠计数、错误处理 |
| [architecture/analysis-worker.md](architecture/analysis-worker.md) | 分析 Worker：状态机、VLM 描述流程、重试策略 |
| [architecture/rule-engine.md](architecture/rule-engine.md) | 规则/反馈引擎：KNN 投票、来源权重、decision_trace |
| [architecture/api-server.md](architecture/api-server.md) | Local API Server：接口约定、分页/采样、鉴权 |
| [architecture/web-ui.md](architecture/web-ui.md) | Web UI：TimelineCanvas、检索页、反馈交互、技术选型 |
| [architecture/mcp-layer.md](architecture/mcp-layer.md) | MCP Layer：工具集合、JSON Schema、隐私边界 |
| [architecture/packaging.md](architecture/packaging.md) | 打包部署：PyInstaller、Electron/Tauri 对比 |

### 存储

| 文件 | 内容 |
|------|------|
| [storage/overview.md](storage/overview.md) | 存储策略：SQLite + 文件系统 + 相似检索索引 |
| [storage/schema.md](storage/schema.md) | 全部数据库表定义与索引建议 |
| [storage/file-layout.md](storage/file-layout.md) | TimeTraceData/ 目录结构与文件命名规范 |
| [storage/vector-search.md](storage/vector-search.md) | 相似检索层：pHash + BK-tree（视觉）、VLM 描述 + FTS5 BM25（语义）、RRF 融合 |

### 隐私

| 文件 | 内容 |
|------|------|
| [privacy/strategy.md](privacy/strategy.md) | 黑名单、暂停模式、隐私模式、MCP/API 约束 |

### 工程化

| 文件 | 内容 |
|------|------|
| [engineering/tech-stack.md](engineering/tech-stack.md) | 数据库 / 前端框架 / 打包方案选型对比表 |
| [engineering/capture-params.md](engineering/capture-params.md) | 采集参数推荐值（min/max 间隔、idle 阈值） |
| [engineering/classification.md](engineering/classification.md) | KNN 权重配置、置信度计算、decision_trace 规范 |
| [engineering/risks.md](engineering/risks.md) | 风险分析与缓解措施（隐私、性能、误判、存储膨胀） |
| [engineering/testing.md](engineering/testing.md) | 测试与验收标准（功能 / 性能 / 稳定性 / 准确率） |
