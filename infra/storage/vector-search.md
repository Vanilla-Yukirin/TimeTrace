# 相似检索层

> 返回 [存储总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 按**视觉相似**（画面像素层面）检索截图：接受查询图，返回感知上近似的帧
- 按**语义相似**（画面内容层面）检索活动：接受自然语言，返回内容相关的帧
- 与元数据过滤组合：时间窗口、应用、分类 可与任一通道叠加
- 向上提供单一融合后的排序结果（多通道时走 RRF）

---

## 双通道设计（核心决策）

TimeTrace 把相似检索拆成两条正交通道：

| 通道 | 指纹类型 | 度量 | 查询形态 | 典型用例 |
|------|---------|------|---------|---------|
| **视觉（pHash）** | 64-bit 感知哈希 | 汉明距离 | 以图搜图 | "找一模一样的画面" |
| **语义（BM25 over VLM desc）** | 文本 | 词项打分 | 文本查询，或图→VLM 描述→文本 | "找做类似的事的时段" |

**为何不用图像 embedding**：换一个 VLM/embedding 模型就要整库重算、存储不可迁移；而 pHash 与 VLM 描述文本都是**模型无关**的（pHash 纯算法，描述文本即使换 VLM 也仍是人类可读的 token）。此决策的代价是视觉检索放弃语义泛化、语义检索放弃像素精准，两者互补由 RRF 弥合。

---

## 视觉通道：pHash + 按天分桶的 BK-tree

实现位置：`src/timetrace/phash_index/`

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

## 语义通道：BM25 over VLM 描述（Phase 1.5 到位）

### 索引形态

- VLM 为每帧生成**结构化描述**（活动 / 场景 / 内容摘要 / 关键文字四字段），写入 `analysis_results.vlm_desc`
- 用 SQLite **FTS5 虚表**承载倒排索引 + BM25 打分：
  ```sql
  CREATE VIRTUAL TABLE frames_fts USING fts5(
      activity, scene, summary, keywords,
      content='analysis_results', content_rowid='rowid'
  );
  ```
- 查询时对每列指定独立权重（`keywords` 最高、`summary` 最低），充分利用 per-column IDF
- 中文分词用 jieba `cut_for_search`（写入与查询双方一致），避免 `unicode61` 对中文不切词的问题

### 当前占位实现

`src/timetrace/api/routes/search.py` 里的 `_bm25_search` 目前走 LIKE 兜底（`vlm_desc` 全为 NULL，返回空列表），FTS5 到位后替换该函数为 `MATCH` 查询即可，调用点不变。

---

## 关键词通道：LIKE over window_title + vlm_desc

纯文本关键词（用户在搜索条输入，或 `/v1/records?q=`）走 LIKE 查询，匹配窗口标题与 VLM 描述，元字符 (`%` / `_`) 在入库前转义。此路径对早期无 VLM 数据的库依然可用，用 `window_title` 就能给出有意义的结果。

---

## RRF 融合

多通道结果（视觉 / 语义 / 关键词）经 Reciprocal Rank Fusion 合并：

```
RRF(doc) = Σ_{channel c} 1 / (k + rank_c(doc))   # k = 60
```

- 只看 rank，不看各通道原始分数，天然解决量纲不一致
- 不命中某通道的条目视为 rank = ∞（贡献 0），不因此被枪毙
- 实现在 [search.py](../../src/timetrace/api/routes/search.py) 的 `_rrf_merge()`

---

## 推荐检索流程

```
用户查询（文本 + 可选参考图 + 筛选器）
  │
  ├── 视觉（有图 & 启用）→ compute_phash → PHashIndex.search(radius, ts_range) → [(dist, sid)]
  ├── 语义（启用）     → _describe_image → BM25 over vlm_desc → [(sid, score)]
  └── 关键词（无语义时）→ LIKE window_title / vlm_desc      → [(sid, score)]
        │
        ▼
  RRF 合并 → 候选集
        │
        ▼
  JOIN screenshots + records + analysis_results 取元数据
        │
        ▼
  post-filter（apps / categories）
        │
        ▼
  top-N 返回 UI / MCP
```

---

## 相关文档

- [存储 Schema（screenshots.phash）](schema.md)
- [Capture Service（pHash 计算落库）](../architecture/capture-service.md)
- [Local API Server（/v1/search/by-image）](../architecture/api-server.md)
- [分析 Worker（VLM 描述生成）](../architecture/analysis-worker.md)
- [开发路线图](../overview/roadmap.md)
