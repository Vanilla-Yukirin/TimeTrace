# TimeTrace 项目 Wiki

> TimeTrace 是一个 **Windows-first、本地优先**的桌面活动记忆层。采集端记录活跃窗口与关键帧，服务端完成 SQLite/文件存储、VLM 描述与分类、混合检索、五层记忆金字塔和 LLM 叙述，再通过 Web、REST 与 bearer-gated MCP 暴露结构化上下文；MCP 不返回原始截图。

---

## 当前状态（2026-11-10 文档核对）

客户端 / 服务端拆分、本地 AI、多设备时间轴与设备级摘要、Agent 读取契约和私人 TS 部署均已有实现与验收记录，代码在 `src/timetrace/{common,client,server}/`。**本页是架构现状入口；生产快照日期和待验收范围看 PLAN，子页可能滞后**，与本页或代码冲突时以代码和最新 devlog 为准。尚未实装的设计点应在所属段落顶部用 `## **⚠️ …**` 标注。
**下一步该做什么看滚动 TODO [`devlogs/PLAN.md`](../devlogs/PLAN.md)。**

### 重构之上已上线的能力

- **本地 LLM 全链路**：VLM（LM Studio）给每帧生 `vlm_desc` + 直接产 flat-6 分类（`work/study/social/entertainment/system/uncategorized`）；分类走 `decide_category` 规则+VLM **加权投票**（KNN 已删除）。worker 同时生文本 embedding 入库。
- **多模态搜索**：records 查询走 FTS5 trigram+BM25；`POST /v1/search/by-image` 走 pHash+BK-tree 与 VLM 描述文本通道，再用 RRF(k=60) 融合；`GET /v1/search/text` 已接通 FTS5/LIKE + 文本 embedding cosine + RRF。图搜语义通道使用描述 embedding，缺 embedder 时退回 LIKE 词重叠；关键词退路仍是 LIKE，向量仍为 numpy 全量扫描。
- **MCP**：`server/mcp_layer/server.py` 是对外工具注册的唯一事实源，挂 `/mcp/`、bearer-gated；`search_summaries` 提供分层叙述，`apply_label` 是唯一写工具。Web Agent 工具集来自 `server/agent/tools.py`，不要用两边的数量推断能力相同。`mcp_layer/tools.py` 是废弃死 stub。
- **登录鉴权系统**：cookie session + bearer 双通道 + admin seed 强制改密 + per-IP 锁定 + token CRUD。详见 [auth-system](architecture/auth-system.md)。
- **Web Agent + AI 看板报告**：有界 tool-calling loop（与 MCP 共享 `server/agent/tools.py`）+ 定时 `report_scheduler` + SSE 流式 + skill 下载；per-app 覆盖（看板准确性 Phase 0）+ 读侧时长封顶 `_clamped_dur_sql`。
- **多设备**：设备身份与 Token 绑定、设置页名称/描述编辑与停用、REST/MCP/Agent 设备筛选、多设备时间轴泳道和逐设备统计均已实现；停用设备与撤销 Token 是两个操作。
- **Agent 读取**：最新活动用 `get_latest_activity`，时段概览用 `get_activity_overview`，精确下钻用 `search_summaries`，逐条回放支持稳定双向分页；缺记录不能推断用户离线。
- **分层记忆金字塔**：固定时间窗 `5min→1h→6h→day→week` 自底向上级联；指标层纯 SQL、叙述层为 summary-of-summaries；`source_hash` 驱动重发，后台 rollup/narrate 默认关闭、生产通过 env 开启；设备级 `device_summaries` 独立级联与叙述，全局按设备拼装；`search_summaries` 支持按时间窗下钻，历史全量覆盖仍需审计。
- **可观测性与页面**：LLM 请求账本已覆盖 worker VLM、narrative 与 MCP `ask_agent`，记录 caller/模型/耗时/token/字符数/错误；report 与 Web Agent streaming 尚未接入。Web 已有 `/audit`、`/pyramid`、`/llm-log` 页面。
- **embserver**：本地 Qwen3-VL 多模态 embedding 守护服务（独立第四入口 + systemd unit），**未接进主 worker/检索**。详见 [embserver](architecture/embserver.md)。
- **私人部署**：Tailscale Serve → `127.0.0.1:19446` nginx → 同 SHA SPA / host-network 容器 API；不保留 Cloudflare 公网回退。Actions 发布不可变 GHCR 镜像，部署机通过 `timetrace-update` 主动拉取、检查和激活。操作统一看 [部署手册](architecture/web-deployment.md)，最近运行证据见 [2026-10-23 归档](../devlogs/infra/archive-202610230447-private-ts-pr24-deployment.md)。

### 重构阶段状态

| 阶段 | 状态 | 核心交付 | 详见 |
|---|---|---|---|
| P0 工程基建 | ✅ | LICENSE / CI / 重构分支 | kickoff devlog |
| P1 目录重组 | ✅ | `src/timetrace/{common,client,server}/` 三层 | kickoff devlog |
| P2 / P2.5 接口抽象 | ✅ | BackendClient / Database / Queue / BlobStorage Protocol | kickoff devlog |
| P3a HTTP + Outbox | ✅ | ingest 幂等、HttpBackend、默认 FIFO 与可选滑动窗口并发，ack 只推进连续完成前缀 | kickoff devlog |
| P3a-5b 双入口接线 | ✅ | OutboxBackend（client 默认 backend）+ 三入口 + bootstrap 共享装配 | kickoff devlog |
| P3b Auth + Init | ✅ | tokens.json 体系 / chmod 600 / init/admin CLI | [p3b3-cli-design](../devlogs/infra/archive-202605171500-p3b3-cli-design.md) |
| P3c 部署设施 | ✅ 历史基线 | deploy.sh / systemd / nginx VPS / frp 是早期已落地但现已退役的链路 | [deployment-architecture（历史）](../devlogs/infra/archive-202605161000-deployment-architecture.md)、[cicd-workflow（历史）](../devlogs/infra/archive-202605161015-cicd-workflow.md) |
| P4 客户端隐私管线 | ⚠️ 仅周边硬化 | Outbox compaction / _safe_close_record / ctypes 长路径完成；**OCR + 区域检测 + 模糊重编码未启**（隐私层仍是 v1 黑名单） | [outbox-compaction](../devlogs/infra/archive-202605171501-outbox-compaction-and-review-fixes.md) |
| P5 容器化 + 适配器 | ⚠️ 容器完成 | Dockerfile / docker-compose / pyproject 平台标记；**PostgresDatabase / RedisQueue / S3BlobStorage 未启** | [packaging-and-container](../devlogs/infra/archive-202605171502-packaging-and-container.md) |
| P6 Headless TUI | ❌ 未启动 | — | — |
| P7 分发自动化 | 🟡 部分完成 | `deploy` push 已发布 server + SPA 单一 GHCR SHA 制品，部署机主动拉取已实现；Windows 客户端已有当前用户级安装器、标签驱动 Release 与 SHA-256 校验，仍缺签名和自动更新 | [pull-based release correction](../devlogs/infra/archive-202608231819-pull-based-docker-release-correction.md)、[Windows package](../packaging/windows/README.md) |

### 阅读约定

- **与现状冲突以代码 + 最新 devlog 为准**：尚未实装的设计点在所属段落顶部加 H2 deprecation 警告。
- **过期标注格式**：`## **⚠️ 一句话标题**`（H2 + 加粗紧贴 emoji，无空格）。递归定位全 infra 的过期标注：`grep -rn '^## \*\*⚠️' infra/`。
- **完整重构脉络看 [kickoff devlog](../devlogs/infra/archive-202605151200-client-server-split-kickoff.md)**；它是历史起点，不是当前阶段状态，当前状态只看本页与滚动 PLAN。

---

## 快速导航

### 入口路径

| 角色 | 从这里开始 |
|------|-----------|
| **新开发者 / 新 agent** | [根 README](../README.md) → 本页「当前状态」→ [滚动 PLAN](../devlogs/PLAN.md) → 再按任务进入下方专题页 |
| **开发验收 / CI / 可观测性** | [工程验收文档](architecture/engineering-workflow.md)：统一检查入口、双容器端到端、日志、readiness 与 metrics |
| **存储 / 数据库** | [存储策略](storage/overview.md) → [数据库 Schema](storage/schema.md) → [相似检索层](storage/vector-search.md) |
| **AI 接入 / MCP** | [MCP Layer](architecture/mcp-layer.md) → [Local API Server](architecture/api-server.md) → [隐私策略](privacy/strategy.md) |
| **鉴权 / 部署** | [登录鉴权系统](architecture/auth-system.md) → [私人部署与运维](architecture/web-deployment.md) → [滚动 PLAN](../devlogs/PLAN.md) |
| **Agent / 记忆架构（已部分上线）** | [分层 schema](storage/pyramid-schema.md) → [写时管线](architecture/episode-and-rollup-pipeline.md) → [薄路由器](architecture/thin-router-agent.md) → [最新叙述层 devlog](../devlogs/backend/archive-202606270722-narrative-loop-search-golive.md) |

---

## 目录

### 🧭 演进设计（部分已实装）

> 这些文档最初是前瞻设计；其中固定时间窗级联、叙述层、`query_stats` 与 `search_summaries` 已落地，episode/signal、deep scan、完整 thin-router 仍是规划。判断完成度以 [滚动 PLAN](../devlogs/PLAN.md) 为准。

| 文件 | 内容 |
|------|------|
| [PLAN-OWNER-REVIEW-2026-10.md](PLAN-OWNER-REVIEW-2026-10.md) | W1–W6 已合入并包含于最近验收的发布版本；保留原设计根因，图标方案仍待确定 |
| [PLAN-BETTER-AGENT.md](PLAN-BETTER-AGENT.md) | **总纲**：分层记忆 + 薄路由器 agent；时间窗金字塔主链已落地，其余仍作演进参考 |
| [storage/pyramid-schema.md](storage/pyramid-schema.md) | `summaries` 五层时间窗、指标/叙述/source_hash/回填与运维说明；`signals` 等扩展仍是规划 |
| [architecture/episode-and-rollup-pipeline.md](architecture/episode-and-rollup-pipeline.md) | 时间窗 rollup 与调度设计；当前实际实现见 `server/summary/` 与 `bootstrap.py` |
| [architecture/thin-router-agent.md](architecture/thin-router-agent.md) | 支撑 spec：agent 重塑为 route→retrieve→light-reason 路由器、分层工具、自描述结果信封、MCP 多入口引导、子 agent map-reduce、前端协同清单 |

### 📋 已落地的计划 / 设计（done-doc）

| 文件 | 内容 |
|------|------|
| [PLAN-MCP-USABILITY.md](PLAN-MCP-USABILITY.md) | 早期 MCP 修复记录；当前分页/时间窗与工具契约已由 10 月实现更新 |
| [PLAN-MULTIPATH-CLIENT.md](PLAN-MULTIPATH-CLIENT.md) | 多路径客户端 P0–P3（已实装）：endpoint failover + 原生 SSH 隧道托管 + 滑动窗口并发上传 + 托盘连接子菜单 |
| [PLAN-REPORT-ACCURACY-FIXES.md](PLAN-REPORT-ACCURACY-FIXES.md) | 历史计划：看板准确性 Phase 0 已上线；其中 pyramid 排期与手动发布待办已经过期，仅用于追溯决策 |

### 概览

| 文件 | 内容 |
|------|------|
| [overview/summary.md](overview/summary.md) | 执行摘要、可量化验收标准、项目定位与非目标 |
| [overview/roadmap.md](overview/roadmap.md) | 已完成主线、当前可靠性阶段和延期研究边界 |

### 架构

| 文件 | 内容 |
|------|------|
| [architecture/overview.md](architecture/overview.md) | 分层原则、进程建议、端到端数据流（Mermaid 图） |
| [architecture/capture-service.md](architecture/capture-service.md) | 采集服务：切窗监听、截图策略、键鼠计数、错误处理 |
| [architecture/analysis-worker.md](architecture/analysis-worker.md) | 分析 Worker：状态机、VLM 描述 + 分类 + embedding 流程、重试/熔断 |
| [architecture/rule-engine.md](architecture/rule-engine.md) | 规则/反馈引擎：规则 + VLM 加权投票（decide_category）、来源权重、decision_trace |
| [architecture/api-server.md](architecture/api-server.md) | Local API Server：接口约定、分页/采样、鉴权 |
| [architecture/web-ui.md](architecture/web-ui.md) | Web UI：TimelineCanvas、检索页、看板/Agent 页、反馈交互、技术选型 |
| [architecture/mcp-layer.md](architecture/mcp-layer.md) | MCP Layer：工具清单、bearer 鉴权、隐私边界；若冲突以 `mcp_layer/server.py` 为准 |
| [architecture/auth-system.md](architecture/auth-system.md) | 登录鉴权系统：cookie session + bearer 双通道、admin seed、token CRUD |
| [architecture/client-server-split.md](architecture/client-server-split.md) | 客户端/服务端三层拆分：common/client/server 职责、wire 协议、backend 实现 |
| [architecture/web-deployment.md](architecture/web-deployment.md) | 当前私人 TS / nginx / GHCR 部署，首次安装、更新器、备份、回滚和鉴权边界 |
| [architecture/embserver.md](architecture/embserver.md) | embserver 子包：本地 Qwen3-VL embedding 守护服务（端口 8766、systemd unit） |
| [architecture/packaging.md](architecture/packaging.md) | 包结构、四个 CLI 入口、Windows Release 与 server 容器分发；旧源码 unit 仅作历史参考 |

### 存储

| 文件 | 内容 |
|------|------|
| [storage/overview.md](storage/overview.md) | 存储策略：SQLite + 文件系统 + 相似检索索引 |
| [storage/schema.md](storage/schema.md) | 全部数据库表定义与索引建议 |
| [storage/file-layout.md](storage/file-layout.md) | TimeTraceData/ 目录结构与文件命名规范 |
| [storage/vector-search.md](storage/vector-search.md) | 相似检索层：pHash + BK-tree、FTS5 BM25、文本 embedding cosine 与 RRF；文本向量已接文本搜索与图搜语义通道，图搜关键词退路仍是 LIKE |

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
