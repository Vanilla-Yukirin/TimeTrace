# 开发路线图

> 返回 [Wiki 首页](../readme.md)

---

## Phase 1 — MVP：记住、回放与视觉检索

**目标**：证明稳定采集、存储、回放、多维检索的可行性，无外部模型依赖。

### 必做功能

| 模块 | 具体内容 |
|------|---------|
| **Capture** | 活跃窗口切换事件 + 关键帧截图（带节流/补帧）；min/max 间隔；idle 段判定；每帧计算 64-bit pHash 落库 |
| **Storage** | SQLite WAL 模式落库；图片文件系统（原图 + 缩略图）；配额删除（只删图不删记录） |
| **相似检索** | pHash + 按天分桶的 BK-tree（视觉通道），关键词 LIKE 回退 |
| **API** | records 查询、时间轴聚合、应用 / 分类多选过滤、关键词匹配、`/v1/search/by-image` 多模态检索 |
| **UI** | 日历 + TimelineCanvas（基础版：单日、少轨道）+ 详情面板；`/search` 页（关键词 + 参考图 + 筛选，行内展开结果） |
| **Privacy** | 托盘一键暂停；应用/标题黑名单；隐私模式（不存图） |

### 验收标准

- 7×24h 连续运行 7 天无崩溃
- CPU ≤ 5%，内存 ≤ 300 MB
- P95 查询 ≤ 300ms（1 天数据）
- 落库成功率 ≥ 99.9%

---

## Phase 1.5 — 轻智能：语义检索与摘要

**目标**：引入 VLM 结构化描述 + FTS5 BM25 打通语义通道，与 Phase 1 的视觉通道通过 RRF 融合；上线 MCP 最小工具集。

### 功能增量

| 功能 | 说明 |
|------|------|
| **VLM 描述** | 每帧生成结构化描述（活动 / 场景 / 内容摘要 / 关键文字四字段），写入 `analysis_results.vlm_desc` |
| **FTS5 + BM25** | 在描述四字段上建 FTS5 虚表，多列加权 BM25 打分（`keywords` 列权重最高）；jieba `cut_for_search` 做中文分词 |
| **语义通道激活** | FTS5 trigram + BM25 `MATCH` 已在 records 关键词搜索落地（`server/db/sqlite.py` `query_records` 的 `records_fts MATCH` + `bm25()` CTE）；但 `/v1/search/by-image` 路由内的 `_bm25_search` 仍是 LIKE 兜底（`server/api/routes/search.py` 的 `_bm25_search`，`records_fts` 表已存在却没接上），属遗留待接线 — **半完成**。上传参考图时走 VLM 描述生成再检索 |
| **摘要** | 手动触发时间段摘要（100–200 字）+ 分类统计（系统算，不让模型瞎编） |
| **MCP** | 已实装工具集（FastMCP，`server/mcp_layer/server.py` → 委托 `server/agent/tools.py` 真实现）：`search_activity`（FTS5 关键词搜索，非 stub）/ `get_recent_activity` / `get_app_breakdown` / `get_category_stats`（SQL 时长聚合，非 stub）/ `apply_label` / `ask_agent`。早期设想的 `list_categories` / `get_activity` 两个工具名从未上线 |

### Analysis Worker 状态机（Phase 1.5）

```
captured → pending_vlm → vlm_done → done
任意阶段失败 → error_retryable（带 retry_count/next_retry_at）或 error_final
```

> 原规划里的 `pending_embed → embed_done` 阶段已移除：
> 视觉相似改走 pHash（不依赖模型），语义相似改走 VLM 描述 + BM25（不存向量），
> 整条 pipeline 不再需要独立的 embedding 步骤。

---

## Phase 2 — 闭环：更准的分类与可迁移的上下文层

**目标**：规则 + VLM 加权投票，用户反馈作为强样本，隐私增强，长期稳定性。

## **⚠️ KNN 已移除，改用规则 + VLM 加权投票**

> 下表早期规划的「KNN 投票」已删除：`server/rules/engine.py` 顶部注释写明 KNN voting was removed。
> 现在 `decide_category`（`server/rules/engine.py`）是规则（权重 2.0）+ VLM（权重 1.5×conf）的加权投票，
> `SOURCE_WEIGHTS` 含 `user_edit=5.0` / `user_confirm=3.0`，用户反馈通过这两个 source weight 直接参与加权投票。
> `decision_trace` 完整化已落地（返回 final_category / confidence / candidates top5 / signals）。

### 功能增量

| 功能 | 说明 |
|------|------|
| **规则/反馈引擎** | 规则 + VLM 加权投票（`decide_category`）、强弱样本权重、decision_trace 完整化 |
| **浏览器细分** | URL 域名 + 画面描述融合策略（浏览器是"多用途应用"） |
| **用户反馈闭环** | 确认/修改→强样本（`user_edit` / `user_confirm` source weight）→影响后续加权投票分类 |
| **隐私增强** | OCR/区域检测后马赛克；或先全局模糊再送 VLM |
| **稳定性** | 长测、崩溃恢复验证、SQLite schema 迁移 |

### 可选后续功能

- 多设备合并（device_id + last_modified）
- 端侧轻量隐私检测
- 浏览器 URL 提取增强
- 条件性 OCR（对 IDE / 终端 / 聊天窗口注入到 VLM prompt；游戏场景关闭以避免稀释）
- 字符 bigram 兜底索引（若 jieba 多粒度召回仍不足）
- 导出到 Markdown / 日记系统

---

## 技术债务清单

| 债务 | 说明 |
|------|------|
| 时间轴组件性能 | TimelineCanvas 大量帧时的渲染优化 |
| Schema 版本管理 | SQLite 迁移策略（Alembic 或手写 migration）；当前 `server/db/sqlite.py::SqliteDatabase._migrate()`（`Database` 是其别名）仍是手写 idempotent ALTER TABLE、无 user_version 的临时方案 |
| Worker 并发限流 | 多 worker 并发上限与 429 退避策略 |
| pHash 索引持久化 | 目前每次启动从 SQLite 全量重建；库量超过 100 万帧时可考虑序列化快照 |

---

## 相关文档

- [执行摘要](summary.md)
- [架构总览](../architecture/overview.md)
- [分析 Worker](../architecture/analysis-worker.md)
