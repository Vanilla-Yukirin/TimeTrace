# TimeTrace 项目 Wiki

> TimeTrace 是一个 **Windows Only、本地优先**的"桌面活动记忆层"。低打扰采集活跃窗口与关键帧截图，落地到 SQLite + 本地文件系统；本地 VLM 给每帧生结构化描述（`vlm_desc`）与 flat-6 分类，支持时间轴回放、多模态搜索（关键词 FTS5 BM25 + 以图搜图 pHash + RRF 融合）、AI 看板报告，并通过 Local API + MCP（6 工具，bearer-gated）对外暴露结构化上下文（MCP 不返回原始截图）。

---

## 当前状态（活文档，持续更新到现状）

客户端 / 服务端三层重构（P0–P3）已完成并生产验证，代码在 `src/timetrace/{common,client,server}/`。**本 wiki 各子页持续更新到现状**（不再是冻结的 v1 快照）；个别尚未实装的设计点在所属段落顶部用 `## **⚠️ …**` 标注。
**下一步该做什么看滚动 TODO [`devlogs/PLAN.md`](../devlogs/PLAN.md)。**

### 重构之上已上线的能力

- **本地 LLM 全链路**：VLM（LM Studio）给每帧生 `vlm_desc` + 直接产 flat-6 分类（`work/study/social/entertainment/system/uncategorized`）；分类走 `decide_category` 规则+VLM **加权投票**（KNN 已删除）。worker 同时生文本 embedding 入库。
- **多模态搜索**：关键词 FTS5 trigram+BM25 ✅、以图搜图 pHash+BK-tree ✅、RRF(k=60) 融合 ✅。语义向量 `vector_search` 已实装但**未接进搜索路由**（沉睡的第三路，见 PLAN）。
- **MCP**：`server/mcp_layer/server.py`（FastMCP，**6 工具**：`search_activity` / `get_recent_activity` / `get_app_breakdown` / `get_category_stats` / `apply_label`（唯一写工具，只打标签）/ `ask_agent`），挂 `/mcp/`、bearer-gated（DNS-rebinding allowlist 放行公网域名）。`mcp_layer/tools.py` 是废弃死 stub。
- **登录鉴权系统**：cookie session + bearer 双通道 + admin seed 强制改密 + per-IP 锁定 + token CRUD。详见 [auth-system](architecture/auth-system.md)。
- **Web Agent + AI 看板报告**：有界 tool-calling loop（与 MCP 共享 `server/agent/tools.py`）+ 定时 `report_scheduler` + SSE 流式 + skill 下载；per-app 覆盖（看板准确性 Phase 0）+ 读侧时长封顶 `_clamped_dur_sql`。
- **embserver**：本地 Qwen3-VL 多模态 embedding 守护服务（独立第四入口 + systemd unit），**未接进主 worker/检索**。详见 [embserver](architecture/embserver.md)。
- **公网部署**：家里 box（NAT 后）+ frp 隧道 + nginx VPS（`timetrace.yukirin.me`）+ `deploy.yml` 工作流。详见 [web-deployment](architecture/web-deployment.md)。

### 重构阶段状态

| 阶段 | 状态 | 核心交付 | 详见 |
|---|---|---|---|
| P0 工程基建 | ✅ | LICENSE / CI / 重构分支 | kickoff devlog |
| P1 目录重组 | ✅ | `src/timetrace/{common,client,server}/` 三层 | kickoff devlog |
| P2 / P2.5 接口抽象 | ✅ | BackendClient / Database / Queue / BlobStorage Protocol | kickoff devlog |
| P3a HTTP + Outbox | ✅ | ingest 路由幂等、HttpBackend、Outbox 严格 FIFO | kickoff devlog |
| P3a-5b 双入口接线 | ✅ | OutboxBackend（client 默认 backend）+ 三入口 + bootstrap 共享装配 | kickoff devlog |
| P3b Auth + Init | ✅ | tokens.json 体系 / chmod 600 / init/admin CLI | [p3b3-cli-design](../devlogs/infra/archive-202605171500-p3b3-cli-design.md) |
| P3c 部署设施 | ✅ | deploy.sh / systemd unit / GH Actions（workflow_dispatch）+ nginx VPS + frp 公网链路 | [deployment-architecture](../devlogs/infra/archive-202605161000-deployment-architecture.md)、[cicd-workflow](../devlogs/infra/archive-202605161015-cicd-workflow.md) |
| P4 客户端隐私管线 | ⚠️ 仅周边硬化 | Outbox compaction / _safe_close_record / ctypes 长路径完成；**OCR + 区域检测 + 模糊重编码未启**（隐私层仍是 v1 黑名单） | [outbox-compaction](../devlogs/infra/archive-202605171501-outbox-compaction-and-review-fixes.md) |
| P5 容器化 + 适配器 | ⚠️ 容器完成 | Dockerfile / docker-compose / pyproject 平台标记；**PostgresDatabase / RedisQueue / S3BlobStorage 未启** | [packaging-and-container](../devlogs/infra/archive-202605171502-packaging-and-container.md) |
| P6 Headless TUI | ❌ 未启动 | — | — |
| P7 分发自动化 | ❌ 未启动 | — | — |

### 阅读约定

- **与现状冲突以代码 + 最新 devlog 为准**：尚未实装的设计点在所属段落顶部加 H2 deprecation 警告。
- **过期标注格式**：`## **⚠️ 一句话标题**`（H2 + 加粗紧贴 emoji，无空格）。递归定位全 infra 的过期标注：`grep -rn '^## \*\*⚠️' infra/`。
- **完整重构脉络看 [kickoff devlog](../devlogs/infra/archive-202605151200-client-server-split-kickoff.md)**（滚动更新的"重构宪法"，所有 PR 应能追溯到其中某个 P）。

---

## 快速导航

### 入口路径

| 角色 | 从这里开始 |
|------|-----------|
| **新开发者** | [概览 → 执行摘要](overview/summary.md) → [架构总览](architecture/overview.md) → [开发路线图](overview/roadmap.md) |
| **存储 / 数据库** | [存储策略](storage/overview.md) → [数据库 Schema](storage/schema.md) → [相似检索层](storage/vector-search.md) |
| **AI 接入 / MCP** | [MCP Layer](architecture/mcp-layer.md) → [Local API Server](architecture/api-server.md) → [隐私策略](privacy/strategy.md) |
| **鉴权 / 部署** | [登录鉴权系统](architecture/auth-system.md) → [公网部署](architecture/web-deployment.md) → [客户端/服务端拆分](architecture/client-server-split.md) |
| **Agent / 记忆架构（规划中）** | [PLAN-BETTER-AGENT.md](PLAN-BETTER-AGENT.md) → [分层 schema](storage/pyramid-schema.md) → [写时管线](architecture/episode-and-rollup-pipeline.md) → [薄路由器](architecture/thin-router-agent.md) |

---

## 目录

### 🧭 规划 / 设计草案（前瞻，未实装）

> 这些是**前瞻性活计划**（非 v1 快照、非 devlog）。描述的是「更好的 Agent」的目标架构，会随推进更新；落地后再整理为 devlog 归档 + 翻新相关 wiki 子页。

| 文件 | 内容 |
|------|------|
| [PLAN-BETTER-AGENT.md](PLAN-BETTER-AGENT.md) | **总纲（先读）**：分层记忆金字塔 + 薄路由器 agent —— 问题陈述与 token 数学、prior-art 对比、金字塔/agent 设计、子 agent fallback、分阶段路线、评估、风险 |
| [storage/pyramid-schema.md](storage/pyramid-schema.md) | 支撑 spec：统一 `summaries` 时间窗级联表（grain 5min/1h/6h/day/week）+ `signals` DDL sketch、压缩率/下钻提示/脱敏字段、幂等键与 watermark、回填、删除传播 |
| [architecture/episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md) | 支撑 spec：写时级联 builder（tumbling 时间窗 rollup / 信号检测）如何挂 worker 状态机与 `bootstrap.serve()` 调度器、降级契约、并发预算 |
| [architecture/thin-router-agent.md](architecture/thin-router-agent.md) | 支撑 spec：agent 重塑为 route→retrieve→light-reason 路由器、分层工具、自描述结果信封、MCP 多入口引导、子 agent map-reduce、前端协同清单 |

### 📋 已落地的计划 / 设计（done-doc）

| 文件 | 内容 |
|------|------|
| [PLAN-MULTIPATH-CLIENT.md](PLAN-MULTIPATH-CLIENT.md) | 多路径客户端 P0–P3（已实装）：endpoint failover + 原生 SSH 隧道托管 + 滑动窗口并发上传 + 托盘连接子菜单 |
| [PLAN-REPORT-ACCURACY-FIXES.md](PLAN-REPORT-ACCURACY-FIXES.md) | 看板报告准确性 Phase 0（已上线）+ per-app 覆盖 + pyramid 落地排期（演示后开始） |

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
| [architecture/analysis-worker.md](architecture/analysis-worker.md) | 分析 Worker：状态机、VLM 描述 + 分类 + embedding 流程、重试/熔断 |
| [architecture/rule-engine.md](architecture/rule-engine.md) | 规则/反馈引擎：规则 + VLM 加权投票（decide_category）、来源权重、decision_trace |
| [architecture/api-server.md](architecture/api-server.md) | Local API Server：接口约定、分页/采样、鉴权 |
| [architecture/web-ui.md](architecture/web-ui.md) | Web UI：TimelineCanvas、检索页、看板/Agent 页、反馈交互、技术选型 |
| [architecture/mcp-layer.md](architecture/mcp-layer.md) | MCP Layer：6 工具、JSON Schema、bearer 鉴权、隐私边界 |
| [architecture/auth-system.md](architecture/auth-system.md) | 登录鉴权系统：cookie session + bearer 双通道、admin seed、token CRUD |
| [architecture/client-server-split.md](architecture/client-server-split.md) | 客户端/服务端三层拆分：common/client/server 职责、wire 协议、backend 实现 |
| [architecture/web-deployment.md](architecture/web-deployment.md) | 公网部署：家里 box + frp 隧道 + nginx VPS + deploy.yml + 安全论证 |
| [architecture/embserver.md](architecture/embserver.md) | embserver 子包：本地 Qwen3-VL embedding 守护服务（端口 8766、systemd unit） |
| [architecture/packaging.md](architecture/packaging.md) | 打包部署：四入口、optional-deps 桶、systemd unit、deploy.sh 五步 |

### 存储

| 文件 | 内容 |
|------|------|
| [storage/overview.md](storage/overview.md) | 存储策略：SQLite + 文件系统 + 相似检索索引 |
| [storage/schema.md](storage/schema.md) | 全部数据库表定义与索引建议 |
| [storage/file-layout.md](storage/file-layout.md) | TimeTraceData/ 目录结构与文件命名规范 |
| [storage/vector-search.md](storage/vector-search.md) | 相似检索层：pHash + BK-tree（视觉）、FTS5 BM25（关键词）、embedding 余弦（语义，未接路由）、RRF 融合 |

### 隐私

| 文件 | 内容 |
|------|------|
| [privacy/strategy.md](privacy/strategy.md) | 黑名单、暂停模式、隐私模式（部分未实装）、MCP/API 约束 |

### 工程化

| 文件 | 内容 |
|------|------|
| [engineering/tech-stack.md](engineering/tech-stack.md) | 数据库 / 前端框架 / 打包方案选型对比表 |
| [engineering/capture-params.md](engineering/capture-params.md) | 采集参数推荐值（min/max 间隔、idle 阈值） |
| [engineering/classification.md](engineering/classification.md) | 分类：规则 + VLM 加权投票、置信度计算、decision_trace 规范 |
| [engineering/risks.md](engineering/risks.md) | 风险分析与缓解措施（隐私、性能、误判、存储膨胀） |
| [engineering/testing.md](engineering/testing.md) | 测试与验收标准（功能 / 性能 / 稳定性 / 准确率） |
