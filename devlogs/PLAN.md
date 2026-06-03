# TimeTrace 滚动 TODO / Plan

**最后更新：** 2026-06-03（演示后：清理过期 infra+PLAN；未完成项总表对着真代码重建）

> 滚动文档，不是历史快照。完成的事项移到 devlog archive 归档，这里只留**未完成**和**未决策**。
>
> 找代码细节去 [`devlogs/README.md`](./README.md) 索引；找架构现状去 [`infra/`](../infra/)；本文档只回答 **"下一步该做什么"**。

---

## 现状（2026-06-03 演示已完成）

三层重构 P0–P3 已生产验证；本地 LLM 全链路（VLM 分类 + 文本 embedding）、**MCP 6 工具**、登录鉴权系统、Web Agent + AI 看板报告、per-app 覆盖（看板准确性 Phase 0）、公网部署（box + frp + nginx VPS）**均已上线并完成周三演示**。架构现状看 [`infra/readme.md`](../infra/readme.md)。

下面是 **2026-06-03 对照真代码重建的未完成项总表**（替换了已截止的 demo-sprint 计划）。

---

## 未完成项（按优先级）

### 🔴 High

1. **语义/文本向量检索接进搜索路由** —— `db.vector_search`（numpy 余弦，`server/db/sqlite.py`）已实装但**全库零调用方**；`/v1/search/by-image` 与 `/v1/records` 的 RRF 融合仍只跑 pHash + FTS5/LIKE 两路，向量是"沉睡的第三路"。**写侧已通**（worker `_embed_and_save` 写 `text_embedding` BLOB），缺读侧接线。出现频次最高的未做项，最该先接。

### 🟡 Medium

2. **`/v1/search/by-image` 的 `_bm25_search` 从 LIKE 切到 FTS5 MATCH** —— `records_fts`(trigram) 表早已建好，但该路由仍走 `vlm_desc` LIKE（`server/api/routes/search.py` docstring 自承待迁），是全仓**唯一未迁的 LIKE 残留**。关键词/records/MCP 路径已用真 FTS5 BM25，只剩这一处。低改动量。
3. **P4 客户端隐私管线** —— OCR + 区域检测/分类器 + 强模糊重编码（原图绝不落盘）；`PrivacyConfig.mode`(off/text_only/full) 的消费方；写非 `normal` 值的 `privacy_level` 上游生产者。当前隐私层仍是 v1 黑名单，`mode` 字段 + `privacy_level` 列是前向占位、运行时不被消费。
4. **VLM 熔断器盲点** —— `/v1/search/by-image` 的 semantic 通道直 `await vlm_client.describe()` 仅 `except VLMError`，**不走 `gate.acquire` 短路**，VLM 挂了会反复打端点。`VLMHealthGate.acquire` 已存在（`server/vlm/health.py`）但该路由未用。真实运行时债务。

### 🟢 Low

5. **图像/多模态 embedding 接进 worker 与检索** —— embserver 子包（端口 8766，Qwen3-VL）已独立实装但未接入主 server worker/检索；worker 只有文本 `EmbeddingClient`。产出未喂搜索。
6. **decision_trace / confidence / category_suggested 运行时持久化** —— 三列 schema 预留，`decide_category` 已算出 trace+confidence 但 worker 丢弃（`server/worker/loop.py`），`set_category_final` 只写 `category_final`。误判可解释性 / 置信度阈值 UI / 金字塔信任门控都依赖它。
7. **基于反馈的高权重样本回灌分类** —— `SOURCE_WEIGHTS` 留 `user_edit=5.0/user_confirm=3.0` 但 `decide_category` 只消费 rule+vlm 两源，feedback 路由只做 before/after 审计、不影响后续分类（KNN 库已删，原回灌目标作废，需重新定义机制，如喂回 rule 表/per-app override）。
8. **存储配额生命周期** —— `settings` 配置 `storage.max_image_days/max_image_gb`，超配额软删最旧图（写 `screenshots.deleted_at` 保留元数据+pHash/向量）+ 配额提示。`deleted_at` 列存在但全库无驱逐逻辑。
9. **双进程模式作为 timetrace-client 默认的收尾（P3b/P4）** —— 三 backend + OutboxBackend→Http 已是 client 默认，但 capture 直写 outbox/blobs 跳过 canonical `screenshots/` 树、上传折进隐私管线等 P4 项未做。单进程仍是稳定默认。
10. **P5 可选适配器** —— PostgresDatabase(asyncpg/pgvector) + RedisQueue + S3BlobStorage；`Database/Queue/Blob` 别名升级为 `typing.Protocol`。全是注释占位。
11. **P6 Headless TUI 客户端 + P7 分发自动化** —— ghcr 自动发布 / release CI；目前只有 `deploy.yml` workflow_dispatch。空占位。
12. **图像缺失类不可恢复错误直转 `error_final`、不消耗 retry 配额** —— `loop.py` 当前 image load 失败统一走 `_fail` 退避重试链，不区分错误类型。
13. **SQLite schema 迁移正式方案** —— `_migrate()` 仍是手写 idempotent ALTER TABLE，无版本号机制（Alembic / PRAGMA user_version）+ 崩溃恢复长测。
14. **`capture_mode=fullscreen` 全屏截图模式实装** —— 配置项可读写持久化，但采集循环恒走 `capture_active_window`，fullscreen 分支不存在（行为上是死配置）。
15. **`app_name` Unknown 残留启发式补救（可选）** —— 416 PID 中 174 仍 Unknown（系统进程权限不允许）。主修已完成（ctypes `QueryFullProcessImageNameW`），可选 GetClassName/window_title 兜底。
16. **YAML/JSON 规则表文件加载器 + 文件热加载（若仍需要）** —— 规则现已从 settings KV per-app overrides 构造（`build_ruleset` + 每任务 `load_overrides` 热加载）。文件加载方案是否仍需，由用户决定。

---

## 分层记忆金字塔（设计完成，演示后开始实装）

> 四篇设计草案已成稿（2026-06-02）且对现状代码锚定准确，但 DB 无对应表、src 无对应模块，**至今未实装**。评审判决：建，但 **gate 在 Phase 0 数据卫生之后**（已落地）。先发 L2 + day digest，推迟 L4 周/月、deep_scan、双 token-budget SSE。

- **金字塔写时层**：`episodes`(L2 会话) / `digests`(L3-L4 日周月上卷) / `signals`(派生行为信号) 三表 + `records.episode_id` 列 + `episodes_fts`；配套 segmenter/builder/rollup/detectors 模块、`_episode_builder_loop`/`_digest_scheduler`、watermark 增量、capped SUM-invariant 测试。落地时时长 clamp 必须引用已实装的 `_clamped_dur_sql()`（>5min 封顶），别照抄裸 `SUM(MAX(0,...))`。
- **Thin-router 6 工具按成本分层重构**：`query_stats/get_episodes/get_digest/search_episodes/get_raw` + `apply_label`，token-bounded by construction；旧名 `search_activity/get_recent_activity` 降为薄 shim；子 agent map-reduce(deep_scan) + runner turn-budget gate(~40K) + SSE 扩展；MCP 工具从 `TOOL_SCHEMAS` 生成消除三处手写 drift；删 `hours_back_hint` 死参数（`runner.py` 声明从不读）。
- **给 4 篇架构文档补评审修正**：①时长封顶引用 `_cap_implausible_record_durations` 别只 `MAX(0,...)`；②episode 分类投票 NULL-aware；③冷启回填含 L1 `category_final` 回填；④报告 persona 去臆造化。
- 设计文档：[PLAN-BETTER-AGENT.md](../infra/PLAN-BETTER-AGENT.md) · [pyramid-schema](../infra/storage/pyramid-schema.md) · [episode-pipeline](../infra/architecture/episode-and-rollup-pipeline.md) · [thin-router](../infra/architecture/thin-router-agent.md)。

---

## 需要用户授权的操作（任何对生产 box DB 的写）

详见 [`infra/PLAN-REPORT-ACCURACY-FIXES.md`](../infra/PLAN-REPORT-ACCURACY-FIXES.md) §3。摘要：

- **扩回填其余 ~2800 条旧 NULL `category_final`** —— 当前报告会诚实显示为"尚未分类/待回填"，不影响准确性；要更满的报告可扩到高频应用规则回填。需 dry-run + 备份。
- **清理旧 20 类两级 taxonomy** —— `DELETE FROM categories`，先核对无记录引用旧 id。优先级低于报告准确性。
- **禁止 ssh box 改 git/重启，一律走 deploy 工作流**（唯一例外见 CLAUDE.md：LM Studio 模型加载、用户显式授权的运维）。

---

## Pivot 决策（历史，要回滚就改这里）

| # | 决策 | 理由 |
|---|---|---|
| 1 | `vlm_desc` 不依赖 VL chat，改用 35BA3B 读 `window_title + app_name + process_name + url` 生 | LM Studio 仓库无 VL chat 模型；35BA3B 文本能力强；窗口标题信息密度足够；便宜 10× |
| 2 | 图像理解走 `qwen3-vl-embedding-2b`（不生文字，只生向量） | 适合以图搜图 / 视觉相似度；不需 chat completion |
| 3 | 文本检索叠 `nomic-embed-text-v1.5` + FTS5 trigram | 文本向量做语义召回，FTS5 做关键词精确，混合排序 |
| 4 | 向量库先用 numpy 内存索引，不立刻上 sqlite-vec | records 量（≤100K）暴力 cosine 够快；数据量大了再上 sqlite-vec |
| 5 | MCP 增 `ask_agent`：内部跑 35BA3B agent 自循环 search | 给外部 agent 一个"高级"接口，单次 tool 调用拿自然语言答案 |
| 6 | 分类删 KNN，改 `decide_category` 规则+VLM 加权投票（6 类单级） | 给小固定分类集 KNN 投票弱、需用户持续打标签不划算；VLM 直接选类 + 规则确定性覆盖更稳 |

---

## 文档 / 约定（持续维护）

- **devlog 写完不改**：`devlogs/**/*.md` 是历史快照；纠正/补充另写新 archive。`infra/` 是活文档可改。
- **deprecation 警告格式**：`## **⚠️ 一句话标题**`（H2 + 加粗紧贴 emoji），`grep -rn '^## \*\*⚠️' infra/` 可枚举。
- **PLAN.md（本文档）是滚动的**：每次会话结束 / 阶段完成时更新。
- **小主机 SSH alias**：内网 `GTi13-Ultra`(192.168.2.105)；中继 `GTi13-Ultra-2v4G`(121.43.33.13) / `GTi13-Ultra-JPVPS`。

---

## 关联入口

- [`devlogs/README.md`](./README.md) — 所有 devlog archive 索引
- [`infra/readme.md`](../infra/readme.md) — 架构 wiki 索引
- [`infra/archive...client-server-split-kickoff.md`](infra/archive-202605151200-client-server-split-kickoff.md) — 重构宪法（P0–P7 路线）
- [`infra/PLAN-REPORT-ACCURACY-FIXES.md`](../infra/PLAN-REPORT-ACCURACY-FIXES.md) — 看板准确性 + pyramid 排期 + box DB 写授权清单
- [`CLAUDE.md`](../CLAUDE.md) — 项目协作约定速读
