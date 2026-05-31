# 执行摘要

> 返回 [Wiki 首页](../readme.md)

TimeTrace 是一个 **Windows Only、本地优先**的桌面活动记忆层。它在后台低打扰地采集你"看过什么、做过什么"——活跃窗口元数据 + 关键帧截图，落地到本地 SQLite + 文件系统，再异步用 VLM 理解内容、用文本 embedding 向量化，最后提供时间轴回放、多模态搜索，以及给 AI Agent 用的 MCP 上下文导出。

核心价值是"**可回放、可检索的桌面行为时间轴**"，而不是录屏，也不是 AI 伴侣主体。

---

## 它解决什么问题

你三天前看过一个网页 / 文档 / 报错，现在只记得"大概是个蓝色的页面，讲到了 X"，却想不起在哪。TimeTrace 让你能用关键词、用截图、用自然语言描述去"翻"自己的数字活动史。

---

## 可量化验收标准

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
- **低打扰**：基于窗口切换 / 内容变化触发采集，不是无脑定时截屏；CPU/内存预算严格约束。
- **多模态检索**：关键词（FTS5 trigram + BM25）+ 以图搜图（pHash BK-tree）+ 语义（VLM 描述），三路经 RRF 融合（`server/retrieval.py:reciprocal_rank_fusion`）。
- **VLM 已实装**（非桩）：后台 worker 调 LM Studio（Qwen3-VL）为截图生成结构化描述，写回 `analysis_results.vlm_desc` + `category_final`。**VLM 产出的描述既进 FTS5 关键词索引、又是文本 embedding 的输入**，已是检索的核心语义通道之一。
- **文本 embedding 已实装**：worker `vlm_done` 后 best-effort 把描述向量化（nomic 768 维，packed float32 BLOB）写进 `analysis_results.text_embedding`，并有一次性回填扫（`_backfill_embeddings`，双失败模式：单行 poison skip / 连续失败 abort）。向量搜索（`db.vector_search`，numpy 余弦全表扫）已就绪，**接线进 search 路由是下一步**（见 [roadmap](roadmap.md) P2c）。
- **登录 + 公网**：bcrypt 用户 + HttpOnly cookie session + bearer token 双通道鉴权（`server/auth.py`，`require_principal`）；已通过家里小主机 + 云 VPS + nginx + frp + Cloudflare 部署到 `timetrace.yukirin.me`（鉴权后才开公网）。
- **Agent 友好**：通过 MCP 暴露 4 个工具（`search_activity` / `get_recent_activity` / `get_app_breakdown` / `ask_agent`），Claude 等可直接调用。

---

## 运行模式（两种入口 + 一个旁路服务，常被混淆）

| 入口 | 状态 | 进程数 | 场景 |
|------|------|--------|------|
| `timetrace`（`main.py`） | ✅ 默认稳定 | 1（capture + worker + api + 托盘） | 单机日常 |
| `timetrace-server` + `timetrace-client` | 🚧 接线中 | 2（client 只采集，server 跑 API+DB+VLM） | 多设备 / 远程 / headless |
| `timetrace-embserver` | ✅ 已实装 | 独立 embedding 服务（端口 8766） | 给 server/client 提供向量化 |

单进程下 capture 经 `InProcessBackend` 直调 `SqliteDatabase` + `PHashIndex`，**不走 HTTP**；双进程下 client 用 `OutboxBackend` → `HttpBackend` POST `/v1/ingest/*`（`client_record_id` + `screenshots(record_id, hash_sha256)` 双 UNIQUE 做幂等）。

---

## 功能清单（全生命周期）

按"采集 → 存储 → 分析 → 检索/回放 → 对外接口"闭环组织：

| 模块 | 功能 |
|------|------|
| **采集** | 活跃窗口切换、窗口标题/进程/应用/URL、关键帧截图、键鼠计数、idle 段判定、每帧 pHash |
| **存储** | SQLite 元数据（WAL，含每帧 pHash + `text_embedding` BLOB）；截图与缩略图本地文件夹；内存 pHash BK-tree 索引 |
| **分析** | VLM 结构化描述（worker 状态机 + 重试 + 跨 worker 熔断）、文本 embedding、分类决策、统计聚合 |
| **检索** | 时间范围/应用/分类过滤；以图搜图（pHash 视觉通道）；关键词/语义（VLM 描述 + FTS5 BM25）；向量召回（`vector_search`，已实装待接线）；多通道 RRF 融合 |
| **回放** | 日历 + 单日时间轴、多轨道视图、hover 详情、点击进入记录详情 |
| **摘要** | 按时间段生成 summary（可缓存，不作为原始数据必存） |
| **隐私** | 黑名单、暂停记录、隐私模式、不存图策略、外部接口不返原图 |
| **鉴权** | bcrypt 登录 + cookie session（admin/admin 首启强制改密 + 登录限速）+ bearer token；token CRUD 经 CLI / 前端 Settings |
| **MCP/API** | 时间段上下文、统计、检索工具、分类列表与 schema |
| **前端** | TimelineCanvas、详情面板、搜索页、设置页（含 embserver detect 面板）、反馈交互；独立 vite 服务，代理 `/v1/*` `/thumbs/*` `/emb` |

---

## 当前优先级

1. **P2c 向量通道接线**：把已实装的 `db.vector_search` 接进 `/v1/search` 的 RRF，形成关键词 + 视觉 + 语义向量三路融合（当前向量是"沉睡的第三路"）。
2. **P3a 双进程接线收尾**：让 `timetrace-client` 默认 `OutboxBackend` 全链路打通。
3. MCP 个别 stub 收尾（`tools.py:get_category_stats`）。
4. P5 PostgreSQL + pgvector（`server/db/__init__.py` 的 `Database` 别名升级为 Protocol 后接入）。

---

## 相关文档

- [开发路线图](roadmap.md)
- [架构总览](../readme.md)
- 开发过程归档（backend / frontend / infra / research）：[devlogs/README.md](../../devlogs/README.md)
