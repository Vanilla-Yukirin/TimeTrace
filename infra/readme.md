# TimeTrace 项目 Wiki

> TimeTrace 是一个 **Windows Only、本地优先**的"桌面活动记忆层"。低打扰采集活跃窗口与关键帧截图，落地到 SQLite + 本地文件系统，支持时间轴回放、搜索与导出；第二阶段引入 VLM/Embedding 智能分析；通过 Local API + MCP 协议对外暴露结构化上下文，默认不返回原始截图。

---

## 快速导航

### 三条入口路径

| 角色 | 从这里开始 |
|------|-----------|
| **新开发者** | [概览 → 执行摘要](overview/summary.md) → [架构总览](architecture/overview.md) → [开发路线图](overview/roadmap.md) |
| **存储 / 数据库** | [存储策略](storage/overview.md) → [数据库 Schema](storage/schema.md) → [向量检索](storage/vector-search.md) |
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
| [architecture/analysis-worker.md](architecture/analysis-worker.md) | 分析 Worker：状态机、VLM/Embedding 流程、重试策略 |
| [architecture/rule-engine.md](architecture/rule-engine.md) | 规则/反馈引擎：KNN 投票、来源权重、decision_trace |
| [architecture/api-server.md](architecture/api-server.md) | Local API Server：接口约定、分页/采样、鉴权 |
| [architecture/web-ui.md](architecture/web-ui.md) | Web UI：TimelineCanvas、检索页、反馈交互、技术选型 |
| [architecture/mcp-layer.md](architecture/mcp-layer.md) | MCP Layer：工具集合、JSON Schema、隐私边界 |
| [architecture/packaging.md](architecture/packaging.md) | 打包部署：PyInstaller、Electron/Tauri 对比 |

### 存储

| 文件 | 内容 |
|------|------|
| [storage/overview.md](storage/overview.md) | 存储策略：SQLite + 文件系统 + 向量层总览 |
| [storage/schema.md](storage/schema.md) | 全部数据库表定义与索引建议 |
| [storage/file-layout.md](storage/file-layout.md) | TimeTraceData/ 目录结构与文件命名规范 |
| [storage/vector-search.md](storage/vector-search.md) | Faiss / sqlite-vec / numpy 对比与推荐检索流程 |

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
