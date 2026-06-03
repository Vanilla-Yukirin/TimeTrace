# 分类引擎设计：权重、置信度与学习闭环

> 返回 [Wiki 首页](../readme.md) | 相关：[规则/反馈引擎](../architecture/rule-engine.md) · [Analysis Worker](../architecture/analysis-worker.md)

本篇是分类决策的**设计与调参视角**；融合算法的代码契约（数据结构、签名、来源、TODO）在 [规则/反馈引擎](../architecture/rule-engine.md)，二者交叉引用，避免重复。

分类引擎已于 `26ccbd5` 接进 Analysis Worker：融合逻辑（[server/rules/engine.py](../../src/timetrace/server/rules/engine.py)::`decide_category`）在 `vlm_done` 时即被调用并落库 `category_final`（[server/worker/loop.py:223](../../src/timetrace/server/worker/loop.py)::`decide_category` → :232 `set_category_final`）。同一次变更把信号收紧为 **rules-first → VLM-pick 两信号**（删除 KNN）、分类体系收紧为**扁平 6 类**。本篇描述的分类体系、权重、阈值即当前生效的设计。

---

## 分类体系

TimeTrace 采用**扁平 6 类**（无子类，id 为英文）：

```
work          工作
study         学习
social        沟通
entertainment 娱乐
system        系统
uncategorized 未分类
```

`category_final` 存的是英文 id，中文是 display label。这套 id 集（`_BUILTIN_CATEGORIES`，[server/db/sqlite.py:215](../../src/timetrace/server/db/sqlite.py)）同时镜像为 VLM 输出 enum（`_CATEGORY_IDS`，[server/vlm/client.py:43](../../src/timetrace/server/vlm/client.py)），两处需同步。

落库列（`analysis_results`）：`category_final`（最终类别）/ `category_suggested`（引擎建议类别）/ `decision_trace`（JSON 可解释痕迹，决定来源记在这里——schema 无独立 `category_source` 列）。`decision_trace` 由引擎生成但当前尚未持久化写入。

---

## 两信号融合

分类决策由两类信号加权投票得出（算法详见 [server/rules/engine.py::decide_category](../../src/timetrace/server/rules/engine.py)）：

1. **规则信号**（确定性，`rule` 2.0）：应用 / 域名 / 标题命中预设规则表（`RuleSet.match`）
2. **VLM 信号**（语义，`vlm` 1.5）：VLM 已直接产 category id（[server/vlm/client.py:166](../../src/timetrace/server/vlm/client.py)，非法值兜底 `uncategorized`），构造 `VlmPrediction`

规则权重 2.0 > VLM 1.5，故命中规则时规则的确定性 outvote VLM 的语义预测；无匹配规则时 VLM 的 pick 胜出。

---

## 权重表设计

| 来源 | 权重 | 依据 |
|------|------|------|
| 用户修改 (user_edit) | 5.0 | 人工标注金标准 |
| 用户确认 (user_confirm) | 3.0 | 人工背书 |
| 规则匹配 (rule) | 2.0 | 确定性高但覆盖有限 |
| VLM 预测 (vlm) | 1.5 | 语义强但有幻觉风险 |

权重写死在 `engine.py::SOURCE_WEIGHTS`，调参时集中改一处。用户信号（5.0 / 3.0）数量级高于模型信号（1.5），保证一次人工修改足以压制弱信号——这是学习闭环"越用越准"的数值基础。

---

## 置信度计算

```python
confidence = top1_score / (top1_score + top2_score)
```

- 取值 `[0.5, 1.0]`：top1 == top2 时为 0.5（完全不确定），单一信号时为 1.0
- 两信号皆无产出时引擎直接返回 `("uncategorized", 0.0, ...)`，不参与阈值判定
- **阈值策略**（建议，接线时落地）：
  - `>= 0.7`：自动采纳
  - `0.5 ~ 0.7`：标记"低置信"，UI 提示用户确认
  - 仅 VLM 单信号且置信度低：进入待复核队列

---

## **⚠️ KNN 已于 26ccbd5 删除，不再是信号源**

下文描述的 KNN 近邻投票通道已整体移除（`engine.py` 模块 docstring：`KNN voting was removed`，`SOURCE_WEIGHTS` 不再含 `knn`），`KnnNeighbor` 类与 `decide_category` 的邻居入参均已不存在。以下小节保留为历史设计记录，不再反映当前实现。

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
feedback 记录（见 sqlite.py feedback 表 schema：id / record_id / action /
              category_before / category_after / tags_before / tags_after /
              user_note / created_at）
      │
      └──► 更新该样本 category_final（来源记在 decision_trace，无 category_source 列）
```

**冷启动**：规则表兜底，用户每次修改都在积累强样本（`user_edit` 权重 5.0），分类准确率随使用提升。`feedback` 路由已存在（[server/api/routes/feedback.py](../../src/timetrace/server/api/routes/feedback.py)），回灌引擎权重的环节尚未接线（KNN 通道已删除，见上）。

---

## 与检索的关系

分类结果（`category_final`）已作为检索的过滤维度之一：检索已支持按 `category_final` 后置过滤（search 路由 `categories` 参数，[server/api/routes/search.py:268](../../src/timetrace/server/api/routes/search.py) → :216 候选过滤；[server/retrieval.py](../../src/timetrace/server/retrieval.py) docstring 亦写 post-filter）。设计上"先按类别缩小候选集再做语义 / 视觉相似排序"（FTS5 BM25 + pHash BK-tree + RRF 融合）仍是未来可优化点。

---

## 当前实现状态

| 模块 | 状态 |
|------|------|
| `decide_category()` 投票逻辑 | ✅ 已实现（`engine.py`），已接线（worker `loop.py:223`） |
| `decision_trace` 生成 | ✅ 已实现（函数返回），❌ 未持久化（schema 列预留） |
| 文本 embedding 生成 | ✅ Worker `_embed_and_save` + `_backfill_embeddings` 已落地 |
| 规则表加载 / 配置 | ⚠️ `RuleSet` 接口就绪，无加载器，默认空表 |
| VLM 类别预测 | ✅ 已实现（`_CATEGORY_IDS` enum schema，`client.py:166-173` 解析 category） |
| 反馈闭环回灌 | ❌ 待 Phase 2（feedback 路由在，回灌引擎权重未接） |

---

## 相关文档

- [规则/反馈引擎（算法契约 + 接线 TODO）](../architecture/rule-engine.md)
- [Analysis Worker（embedding 生成 / 未来分类触发点）](../architecture/analysis-worker.md)
- [开发路线图](../overview/roadmap.md)
