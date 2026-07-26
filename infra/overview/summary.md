# 执行摘要

> 返回 [Wiki 首页](../readme.md)

TimeTrace 是一个 **Windows Only、本地优先**的桌面活动记忆层。它在后台低打扰地采集你"看过什么、做过什么"——活跃窗口元数据 + 关键帧截图，落地到本地 SQLite + 文件系统，再异步用 VLM 理解内容、用文本 embedding 向量化，最后提供时间轴回放、多模态搜索，以及给 AI Agent 用的 MCP 上下文导出。

核心价值是"**可回放、可检索的桌面行为时间轴**"，而不是录屏，也不是 AI 伴侣主体。

---

## 它解决什么问题

你三天前看过一个网页 / 文档 / 报错，现在只记得"大概是个蓝色的页面，讲到了 X"，却想不起在哪。TimeTrace 让你能用关键词、用截图、用自然语言描述去"翻"自己的数字活动史。

---

## 可量化验收目标（尚未形成持续基准）

下表是设计目标，不是当前实测成绩；仓库尚缺连续 7 天 CPU、内存、查询 P95 和每日磁盘增长报告。

| 维度 | 目标 |
|------|------|
| **稳定性** | Windows 常驻运行 7×24h，连续 7 天无崩溃 |
| **CPU** | 全程平均 CPU ≤ 5%；高频切窗场景下无明显卡顿 |
| **内存** | 常驻内存 ≤ 300 MB |
| **查询延迟** | 1 天数据（≥10,000 事件/关键帧）时间轴查询与筛选 P95 ≤ 300ms |
| **数据完整性** | 窗口切换事件与截图关键帧落库成功率 ≥ 99.9% |
| **崩溃恢复** | 异常中断后重启可从 SQLite 状态机恢复分析进度（任务不丢、可重试、可标记失败） |

---

## 核心特征

- **Windows Only**：使用 pywin32 / ctypes 访问 Windows 窗口 API（`QueryFullProcessImageNameW` 直取进程名）。
- **本地优先**：数据默认存在本机 `~/TimeTraceData/`（不在仓库内）；公网访问需鉴权后开启。
- **降低打扰的设计**：基于窗口切换 / 内容变化触发采集，不做无脑高频录屏；CPU/内存长期基准仍待补。
- **多模态检索**：关键词（FTS5 trigram + BM25）+ 以图搜图（pHash BK-tree）+ 语义（VLM 描述），三路经 RRF 融合（`server/retrieval.py:reciprocal_rank_fusion`）。
- **VLM 已实装**（非桩）：后台 worker 调 LM Studio（Qwen3-VL）为截图生成结构化描述，写回 `analysis_results.vlm_desc` + `category_final`。**VLM 产出的描述既进 FTS5 关键词索引、又是文本 embedding 的输入**，已是检索的核心语义通道之一。
- **文本 embedding 已接生产搜索**：worker best-effort 写 `analysis_results.text_embedding`；`GET /v1/search/text` 调 `db.vector_search`（numpy cosine）并与关键词结果做 RRF，embedding 不可用时降级 keyword-only。
- **登录 + 公网**：bcrypt 用户 + HttpOnly cookie session + bearer token 双通道鉴权（`server/auth.py`，`require_principal`）；已通过家里小主机 + 云 VPS + nginx + frp + Cloudflare 部署到 `timetrace.yukirin.me`（鉴权后才开公网）。
- **分层记忆**：`5min→1h→6h→day→week` 指标与 LLM 叙述自底向上级联，`search_summaries` 可先读 day/week 再按时间窗下钻。
- **Agent 友好**：MCP 提供检索、统计、分层叙述、标签修改和高层问答；`apply_label` 是唯一写工具。实时清单以 `server/mcp_layer/server.py` 为准。
- **可观测与发布**：分析审计、LLM 请求账本、金字塔/LLM 页面已上线；`deploy` push 并行发布 box 后端与 xcy SPA。

---

## 运行模式（两种入口 + 一个旁路服务，常被混淆）

| 入口 | 状态 | 进程数 | 场景 |
|------|------|--------|------|
| `timetrace`（`main.py`） | ✅ 默认稳定 | 1（capture + worker + api + 托盘） | 单机日常 |
| `timetrace-server` + `timetrace-client` | ✅ 已生产使用 | 2（client 采集/outbox，server 跑 API+DB+VLM） | 多设备 / 远程 / headless |
| `timetrace-embserver` | 🧪 独立实验服务 | Qwen3-VL 图像 embedding（端口 8766） | 已实现但尚未接主 worker/检索 |

单进程下 capture 经 `InProcessBackend` 直调 `SqliteDatabase` + `PHashIndex`，**不走 HTTP**；双进程下 client 用 `OutboxBackend` → `HttpBackend` POST `/v1/ingest/*`（`client_record_id` + `screenshots(record_id, hash_sha256)` 双 UNIQUE 做幂等）。

---

## 功能清单（全生命周期）

按"采集 → 存储 → 分析 → 检索/回放 → 对外接口"闭环组织：

| 模块 | 功能 |
|------|------|
| **采集** | 活跃窗口切换、窗口标题/进程/应用/URL、关键帧截图、键鼠计数、idle 段判定、每帧 pHash |
| **存储** | SQLite 元数据（WAL，含每帧 pHash + `text_embedding` BLOB）；截图与缩略图本地文件夹；内存 pHash BK-tree 索引 |
| **分析** | VLM 结构化描述（worker 状态机 + 重试 + 跨 worker 熔断）、文本 embedding、分类决策、统计聚合 |
| **检索** | 时间范围/应用/分类过滤；pHash 图搜；FTS5/LIKE 关键词；`/v1/search/text` 文本向量召回；多通道 RRF |
| **回放** | 日历 + 单日时间轴、多轨道视图、hover 详情、点击进入记录详情 |
| **摘要** | 五层固定时间窗指标级联 + LLM summary-of-summaries；source_hash 重发；backfill/narrate CLI |
| **隐私** | 黑名单、暂停记录、隐私模式、不存图策略、外部接口不返原图 |
| **鉴权** | bcrypt 登录 + cookie session（admin/admin 首启强制改密 + 登录限速）+ bearer token；token CRUD 经 CLI / 前端 Settings |
| **MCP/API** | MCP 检索/统计/摘要/问答、REST API、bearer 鉴权；MCP 不返回原始截图 |
| **前端** | 时间轴、搜索、设置、Agent、报告、审计、金字塔、LLM 请求日志；Vite 构建后由 xcy nginx 托管 |

---

## 当前优先级

当前从功能扩张切换到可靠性和可控性：先收敛 main/deploy 与事实文档，再验证客户端 endpoint failover、隐私/存储生命周期和 schema 恢复；Classifier V2 先做可复现 shadow 评估，不直接改生产分类。唯一动态清单见 [滚动 PLAN](../../devlogs/PLAN.md)，本页不再复制 TODO。

---

## 相关文档

- [开发路线图](roadmap.md)
- [架构总览](../readme.md)
- 开发过程归档（backend / frontend / infra / research）：[devlogs/README.md](../../devlogs/README.md)
