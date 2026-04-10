# 向量检索层

> 返回 [存储总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 存储与索引 embedding（帧级文本描述 + 可选图文混合）
- 执行相似检索（top-k）
- 与元数据过滤组合：先过滤候选集，再向量检索（减少计算量）

---

## 方案对比

| 方案 | 形态 | 优点 | 风险 / 代价 | 适用阶段 |
|------|------|------|-----------|---------|
| **numpy 暴力检索** | 内存计算 | 零额外依赖，实现最快 | 数据量大后线性慢（>10 万向量后明显） | Phase 1.5 原型 |
| **Faiss** | 本地索引库 | 高效相似搜索/聚类，Python wrapper，支持大规模 | 需管理索引文件与增量更新策略 | Phase 1.5–2 **主推** |
| **sqlite-vec** | SQLite 扩展 | 向量与元数据同库，单文件部署 | pre-v1，可能 breaking；扩展加载/兼容性成本 | Phase 2 **可选** |

**推荐演进路径**：numpy（原型验证）→ Faiss（规模化）→ sqlite-vec（可选统一）

---

## 推荐检索流程（高性价比）

```
用户输入 query_text
  │
  ▼
Embedding Provider → query_vec (float32 array)
  │
  ▼
① 元数据过滤（SQLite）
   WHERE ts_start BETWEEN ? AND ?
   AND category_final IN (...)
   AND app_name IN (...)
  → 候选 record_id 列表（缩小向量检索范围）
  │
  ▼
② 向量 top-k 检索（Faiss / numpy）
   vecs = vec_store.load(candidate_ids)
   scores = cosine_sim(vecs, query_vec)
   → top_k 结果
  │
  ▼
③ 回表取详情（SQLite）
   records + analysis_results + screenshots.thumb_path
  → 最终返回给 UI / MCP
```

---

## embeddings 元数据映射表

存储在 SQLite，记录向量在物理存储中的位置：

| 字段 | 类型 | 说明 |
|------|------|------|
| `record_id` | TEXT FK | → records.id |
| `embedding_type` | TEXT | `frame_text` / `frame_image_text` |
| `dim` | INTEGER | 向量维度 |
| `store` | TEXT | `faiss` / `file` / `sqlite_vec` |
| `pointer` | TEXT | 文件偏移 / Faiss 内部 ID / sqlite-vec rowid |
| `updated_at` | INTEGER | — |

---

## Faiss 使用要点

- **索引类型**：Phase 1.5 初始使用 `IndexFlatL2`（暴力精确，无需训练）；规模化后迁移 `IndexIVFFlat`
- **增量更新**：每次新增向量后追加到索引，定期重建（避免碎片）
- **持久化**：使用 `faiss.write_index()` 保存到 `TimeTraceData/` 下的 `.faiss` 文件
- **并发**：Faiss 读操作线程安全；写操作需加锁

---

## 代码示例

```python
def search(query_vec, candidate_ids, vec_store, k=20):
    vecs = vec_store.load(candidate_ids)       # (N, dim) float32
    scores = cosine_sim(vecs, query_vec)       # (N,)
    top_idx = scores.argsort()[-k:][::-1]
    return [(candidate_ids[i], scores[i]) for i in top_idx]
```

---

## 相关文档

- [分析 Worker（生成 embedding）](../architecture/analysis-worker.md)
- [MCP Layer（调用 search_activity）](../architecture/mcp-layer.md)
- [开发路线图（Phase 1.5 引入）](../overview/roadmap.md)
