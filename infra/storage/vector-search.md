# 相似检索层

> 返回 [存储总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 按**视觉相似**（画面像素层面）检索截图：接受查询图，返回感知上近似的帧
- 按**语义相似**（画面内容层面）检索活动：接受自然语言，返回内容相关的帧
- 与元数据过滤组合：时间窗口、应用、分类 可与任一通道叠加
- 向上提供单一融合后的排序结果（多通道时走 RRF）

---

## 三类召回（当前实现）

TimeTrace 同时保留像素相似、关键词相关和文本语义三类信号；不同入口选择其中两路再用 RRF 融合：

| 通道 | 指纹类型 | 度量 | 查询形态 | 典型用例 |
|------|---------|------|---------|---------|
| **视觉（pHash）** | 64-bit 感知哈希 | 汉明距离 | 以图搜图 | "找一模一样的画面" |
| **关键词（FTS5/LIKE）** | 标题/应用/URL/VLM 描述 | BM25 或子串 | 文本，或图→VLM 描述→文本 | "找出现过某个词/产品的时段" |
| **文本语义（embedding）** | `vlm_desc` 文本向量 | cosine | 自然语言文本 | "找表达不同但意思相近的活动" |

**为何不用图像 embedding**：换一个 VLM/embedding 模型就要整库重算、存储不可迁移；而 pHash 与 VLM 描述文本都是**模型无关**的（pHash 纯算法，描述文本即使换 VLM 也仍是人类可读的 token）。此决策的代价是视觉检索放弃语义泛化、语义检索放弃像素精准，两者互补由 RRF 弥合。

---

## 视觉通道：pHash + 按天分桶的 BK-tree

实现位置：`src/timetrace/server/phash_index/`

### 指纹

- 32×32 灰度 + DCT-II（numpy 纯算）→ 取 8×8 低频 → 中位数阈值 → 64-bit int
- 存储字段：`screenshots.phash BLOB`（8 字节，`to_bytes(8, "big")`）
- 采集侧在写图完成后计算并落库（见 [Capture Service](../architecture/capture-service.md)）

### 索引结构

- **BK-tree**（`bk_tree.py`）：离散度量空间的树，三角不等式剪枝；仅做"半径内所有点"的范围搜索
- **按天分桶**（`index.py`）：`_day_key(ts_ms) = ts_ms // 86_400_000`，每个桶一棵独立 BK-tree
- **时间范围查询**：请求带 `ts_range` 时，**只遍历现有 bucket 中 day-key 落在范围内的那几棵**，复杂度 `O(bucket_count)`；不带时间范围则扫全部桶
- **Top-k**：每桶范围搜索后合并，按汉明距离升序截断

### 生命周期

- SQLite 是 source of truth；`PHashIndex` 是派生的内存结构
- 进程启动时 `PHashIndex.from_db(db)` 全量重建（10 万图亚秒级）
- 采集新帧 → 写 DB 成功 → 同步 `index.insert(screenshot_id, phash, ts_start)` 保持热态
- 重启后从 DB 重建；索引本身**不持久化**

### 不用 Faiss / numpy 暴力检索

- Faiss 是为欧式/余弦空间设计的，**不支持汉明距离作为一等公民**（有 IndexBinary 但使用成本不划算）
- 64-bit 汉明 + 树结构剪枝在 TimeTrace 体量下比暴力扫描更省 CPU
- 且 BK-tree 在无需 ANN 近似的前提下给出**精确**的半径内结果

---

## 语义通道：VLM 描述 + FTS5 BM25（已实装）

### 已实装：VLM 描述写入 `analysis_results.vlm_desc`

VLM 为每帧产出**四字段**结构化描述（[src/timetrace/server/vlm/client.py](../../src/timetrace/server/vlm/client.py)）：

```python
{
    "keywords":    list[str],   # 截图中显著可见的文字、应用、产品、人名（≤8）
    "summary":     str,         # ≤30 字画面要点
    "description": str,         # ≤100 字完整描述
    "category":    str,         # 6 类 enum（work/study/social/entertainment/system/uncategorized）
}
```

`category` 不进 `format_description` 拼接文本，而是喂给 worker 的 `decide_category` 加权投票（VLM 分类与规则分类一起表决出 `category_final`）。

落库前由 `format_description()` 拼成 LIKE-friendly 多行文本：

```
{description}

摘要：{summary}
关键词：{kw1}、{kw2}、…
```

写作规范在 prompt 中由硬约束保证 summary / description **以名词性短语开头**，禁止"该截图…"、"画面显示…"、"这是…" 等元叙述句式——一旦每条描述都含这些高频词，FTS5 的 IDF 会被稀释、LIKE 通道也会出现假命中。

### 当前查询路径：LIKE（仅 by-image 路由残留）

[src/timetrace/server/api/routes/search.py](../../src/timetrace/server/api/routes/search.py) 的 `_bm25_search` 目前走 LIKE 兜底（`vlm_desc LIKE '%token%' ESCAPE '\'`），按 `vlm_desc` 命中数粗排。多字段 / per-column 权重在 LIKE 路径上无法表达，但 `format_description` 拼接形态已经把多段拼成一段、LIKE 天然贯穿。

注意：这条 LIKE 兜底**仅存于 `/v1/search/by-image` 的 `_bm25_search`**；关键词 / `/v1/records?q=` / MCP 路径都已切到 FTS5 BM25（`records_fts`，见下）。这是当前全仓唯一还在等待迁移到 MATCH 查询的 LIKE 残留。

## **⚠️ 此节计划已被 records_fts(trigram BM25) 取代**

FTS5 BM25 已经落地，但形态与下方原计划不同，落地实况是：

- 倒排索引建在 **`records_fts`**（建在 records 视角，含 `window_title / app_name / process_name / url / vlm_desc` 5 字段），不是 `frames_fts`（[src/timetrace/server/db/sqlite.py](../../src/timetrace/server/db/sqlite.py) `CREATE VIRTUAL TABLE records_fts`）
- tokenizer 用 **`trigram`**（CJK-friendly，无需 whitespace 切词），**不是 jieba**；中文检索已工作，只是走 trigram 不是分词
- 排序用 `bm25(records_fts)`（同一查询层内调 FTS5 辅助函数），见 `query_records` 的 `use_fts` 分支
- per-column 独立权重未实现
- 此 FTS5 BM25 仅服务关键词 / `/v1/records?q=` / MCP 路径；`/v1/search/by-image` 的 `_bm25_search` 仍是 LIKE 兜底（见上）
- 触发条件：关键词 ≥3 字走 FTS5 trigram MATCH，<3 字退回多字段 LIKE（trigram 需 3+ 字才能匹配，`_FTS_MIN_LEN=3`）

下面是原始计划（保留作历史）：

### 计划：FTS5 BM25

- 用 SQLite **FTS5 虚表**承载倒排索引 + BM25：
  ```sql
  CREATE VIRTUAL TABLE frames_fts USING fts5(
      keywords, summary, description,
      content='analysis_results', content_rowid='rowid'
  );
  ```
- 查询时对每列指定独立权重（`keywords` 最高、`description` 最低）
- 中文分词用 jieba `cut_for_search`（写入与查询双方一致），避免 `unicode61` 对中文不切词的问题
- 切换时只替换 `_bm25_search` 实现，调用点不变

---

## 关键词通道：FTS5 trigram BM25（≥3 字）/ 多字段 LIKE（<3 字）

纯文本关键词（用户在搜索条输入，或 `/v1/records?q=`）：≥3 字走 **FTS5 trigram BM25**（`records_fts`，覆盖 `window_title / app_name / process_name / url / vlm_desc` 5 字段、`bm25()` 排序），<3 字退回**多字段 LIKE**（同 5 字段，元字符 `%` / `_` 在入库前转义），让 "VS" / "鸣潮" 这类短词仍能命中（trigram 需 3+ 字）。此路径对早期无 VLM 数据的库依然可用，用 `window_title` 就能给出有意义的结果。

---

## 向量通道：文本 embedding 余弦（已接 `/v1/search/text`）

文本 embedding 写侧由 worker 的 `EmbeddingClient` 生成并写入 `analysis_results.text_embedding`；读侧由 `GET /v1/search/text` 取查询向量，调用 `SqliteDatabase.vector_search()` 做 numpy cosine，再与关键词结果通过 `retrieval.reciprocal_rank_fusion()` 融合。`create_app` 与 `bootstrap` 已把同一个 embedding client 注入 `app.state`；端点未配置或 embedding 请求失败时，路由优雅降级为 keyword-only。

这条生产链路使用 OpenAI-compatible 文本 embedding 端点；独立的 `embserver`/Qwen3-VL 图像 embedding 尚未接入主 worker 或检索。当前向量读路径是全量 numpy 扫描，只在现有个人数据规模验证过；增长前先补 10K/50K/100K 基准，再评估 sqlite-vec/pgvector。

---

## RRF 融合

多通道结果（视觉 / 语义 / 关键词）经 Reciprocal Rank Fusion 合并：

```
RRF(doc) = Σ_{channel c} 1 / (k + rank_c(doc))   # k = 60
```

- 只看 rank，不看各通道原始分数，天然解决量纲不一致
- 不命中某通道的条目视为 rank = ∞（贡献 0），不因此被枪毙
- 通用实现在 [retrieval.py](../../src/timetrace/server/retrieval.py) 的 `reciprocal_rank_fusion()`；图搜路由仍保留自己的结果整形逻辑

---

## 推荐检索流程

```
文本查询 → FTS5/LIKE ─┐
                    ├→ RRF → enrichment/filter → `/v1/search/text`
          embedding ─┘

参考图   → pHash ─────┐
                    ├→ RRF → enrichment/filter → `/v1/search/by-image`
      VLM 描述→LIKE ──┘
```

---

## 相关文档

- [存储 Schema（screenshots.phash）](schema.md)
- [Capture Service（pHash 计算落库）](../architecture/capture-service.md)
- [Local API Server（/v1/search/by-image）](../architecture/api-server.md)
- [分析 Worker（VLM 描述生成）](../architecture/analysis-worker.md)
- [开发路线图](../overview/roadmap.md)
