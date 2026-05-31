# 分类引擎设计：权重、置信度与学习闭环

> 返回 [Wiki 首页](../readme.md) | 相关：[规则/反馈引擎](../architecture/rule-engine.md) · [Analysis Worker](../architecture/analysis-worker.md)

本篇是分类决策的**设计与调参视角**；融合算法的代码契约（数据结构、签名、来源、TODO）在 [规则/反馈引擎](../architecture/rule-engine.md)，二者交叉引用，避免重复。

## **⚠️ 引擎已实现，但尚未接进流水线**

融合逻辑（[server/rules/engine.py](../../src/timetrace/server/rules/engine.py)::`decide_category`）已写好并有单测，但 **src 内无生产调用方**。当前 Analysis Worker 在 `vlm_done` 后只写**描述文本**（`vlm_desc`）+ **文本向量**（`text_embedding`），不产出类别。本篇描述的分类体系、权重、阈值是引擎被接线后生效的设计——把它接进 Worker 是 Phase 2 的工作。

---

## 分类体系

TimeTrace 采用**两级分类**：

```
大类 (category)
├── 工作
│   ├── 编程
│   ├── 文档
│   └── 会议
├── 学习
│   ├── 看视频
│   ├── 读论文
│   └── 做练习
├── 娱乐
│   ├── 游戏
│   ├── 视频
│   └── 社交
└── 其他
```

落库列（`analysis_results`）：`category_final`（最终类别）/ `category_suggested`（引擎建议类别）/ `decision_trace`（JSON 可解释痕迹，决定来源记在这里——schema 无独立 `category_source` 列）。`decision_trace` 由引擎生成但当前尚未持久化写入。

---

## 三信号融合

分类决策由三类信号加权投票得出（算法详见 [server/rules/engine.py::decide_category](../../src/timetrace/server/rules/engine.py)）：

1. **规则信号**：确定性最高，应用 / 域名 / 标题命中预设规则表（`RuleSet.match`）
2. **VLM 信号**：语义理解，对截图内容的类别预测 + 置信度（`VlmPrediction`）。**注意**：当前 VLM 只产描述文本，类别预测通道待接
3. **KNN 信号**：历史相似样本投票（`KnnNeighbor`）。来源 TBD——候选是文本向量近邻（`text_embedding` + `db.vector_search` 余弦）或 pHash 视觉近邻，目前两者都未接成分类输入

---

## 权重表设计

| 来源 | 权重 | 依据 |
|------|------|------|
| 用户修改 (user_edit) | 5.0 | 人工标注金标准 |
| 用户确认 (user_confirm) | 3.0 | 人工背书 |
| 规则匹配 (rule) | 2.0 | 确定性高但覆盖有限 |
| VLM 预测 (vlm) | 1.5 | 语义强但有幻觉风险 |
| KNN 投票 (knn) | 1.0 | 数据驱动，依赖样本量 |

权重写死在 `engine.py::SOURCE_WEIGHTS`，调参时集中改一处。用户信号（5.0 / 3.0）数量级高于模型与数据信号（1.5 / 1.0），保证一次人工修改足以压制弱信号——这是学习闭环"越用越准"的数值基础。

---

## 置信度计算

```python
confidence = top1_score / (top1_score + top2_score)
```

- 取值 `[0.5, 1.0]`：top1 == top2 时为 0.5（完全不确定），单一信号时为 1.0
- 三信号皆无产出时引擎直接返回 `("uncategorized", 0.0, ...)`，不参与阈值判定
- **阈值策略**（建议，接线时落地）：
  - `>= 0.7`：自动采纳
  - `0.5 ~ 0.7`：标记"低置信"，UI 提示用户确认
  - 仅 VLM 单信号且置信度低：进入待复核队列

---

## KNN 来源（TBD）

`decide_category` 把邻居当入参，不自查库。两条候选数据通路（接线时二选一或融合）：

- **文本向量近邻**：Worker 已把 VLM 描述向量化存进 `analysis_results.text_embedding`（768d packed float32），`db.vector_search`（numpy 余弦全表扫）已实装；取 top-k，邻居已知类别 + 余弦距离构造 `KnnNeighbor`
- **视觉近邻**：采集侧 pHash 的 BK-tree 汉明距离近邻

邻居的 `source` / `confirm_weight` 决定其票重，被人工确认过的样本天然成为高权重邻居——这是反馈闭环回灌的具体形态。

---

## 反馈闭环（Phase 2）

```
用户在 UI 修改类别
      │
      ▼
feedback 记录 (record_id, old_cat, new_cat, source=user_edit, ts)
      │
      ├──► 更新该样本 category_final + category_source=user_edit
      │
      └──► 作为高权重邻居（user_edit=5.0）回灌 KNN 投票库
```

**冷启动**：规则表兜底，用户每次修改都在为 KNN 库积累强样本，分类准确率随使用提升。`feedback` 路由已存在（[server/api/routes/feedback.py](../../src/timetrace/server/api/routes/feedback.py)），但回灌 KNN 库的环节尚未接线。

---

## 与检索的关系

分类结果（`category_final`）设计上作为检索的过滤维度之一：先按类别缩小候选集，再做语义 / 视觉相似排序。当前检索（FTS5 BM25 + pHash BK-tree + RRF 融合，见 [server/retrieval.py](../../src/timetrace/server/retrieval.py)）尚未把 `category_final` 用作过滤——因为它还没被引擎写入。

---

## 当前实现状态

| 模块 | 状态 |
|------|------|
| `decide_category()` 投票逻辑 | ✅ 已实现（`engine.py`），但**无生产调用方** |
| `decision_trace` 生成 | ✅ 已实现（函数返回），❌ 未持久化（schema 列预留） |
| 文本 embedding 生成 | ✅ Worker `_embed_and_save` + `_backfill_embeddings` 已落地 |
| 规则表加载 / 配置 | ⚠️ `RuleSet` 接口就绪，无加载器，默认空表 |
| VLM 类别预测 | ⚠️ 当前 VLM 只产描述文本，类别预测通道待接 |
| KNN 近邻投票 | ⚠️ `vector_search` 已实装但未作为分类输入；来源 TBD |
| 反馈闭环回灌 | ❌ 待 Phase 2（feedback 路由在，回灌未接） |

---

## 相关文档

- [规则/反馈引擎（算法契约 + 接线 TODO）](../architecture/rule-engine.md)
- [Analysis Worker（embedding 生成 / 未来分类触发点）](../architecture/analysis-worker.md)
- [开发路线图](../overview/roadmap.md)
