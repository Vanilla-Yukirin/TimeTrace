# Rule / Feedback Engine

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

规则 / 反馈引擎（[src/timetrace/server/rules/engine.py](../../src/timetrace/server/rules/engine.py)）把三类异质信号融合成一个最终类别 + 置信度 + 可解释 trace：

- **规则分类**：应用名 / URL 域名 / 标题关键词 → 类别映射（`RuleSet.match`）
- **融合**：规则 + VLM 建议 + KNN 投票加权汇总（`decide_category`）
- **可解释性**：输出 `decision_trace`（JSON）供调参与误判排查
- **反馈闭环（设计中）**：用户确认 / 修改产生高权重样本，回灌 KNN 投票库

## **⚠️ 引擎已实现但当前无调用方**

`decide_category()` / `RuleSet` 等函数与数据结构已完整实现并有单测（[tests/test_rules.py](../../tests/test_rules.py)），**但 src 内没有任何生产代码调用它**——Analysis Worker 在 `vlm_done` 之后并不会自动 classify。也就是说：当前流水线 VLM 只产出**描述文本**（写 `vlm_desc`），不产出类别；`analysis_results.category_final` / `decision_trace` 等列存在于 schema 但运行时不被这条引擎写入。下文描述的是**引擎本身的契约**，把它接进 Worker 是 Phase 2 的工作（见末尾 TODO）。

---

## 数据结构

```python
@dataclass
class VlmPrediction:
    category: str
    confidence: float

@dataclass
class KnnNeighbor:
    category: str
    distance: float          # 越小越近
    source: str              # user_edit / user_confirm / rule / vlm / knn
    confirm_weight: float = 1.0

@dataclass
class RuleSet:
    rules: list[dict] = field(default_factory=list)
    def match(self, app, url, title) -> str | None: ...
```

---

## 投票算法（decide_category）

签名：

```python
def decide_category(
    app: str, url: str | None, title: str,
    vlm_pred: VlmPrediction | None,
    knn_neighbors: list[KnnNeighbor],
    rules: RuleSet,
) -> tuple[str, float, dict]:   # (final_category, confidence, decision_trace)
```

### 来源权重（`SOURCE_WEIGHTS`）

| 来源 | 权重 |
|------|------|
| `user_edit` | 5.0 |
| `user_confirm` | 3.0 |
| `rule` | 2.0 |
| `vlm` | 1.5（× 模型置信度） |
| `knn` | 1.0 |

权重写死在 `engine.py::SOURCE_WEIGHTS`，调参集中改一处。

### 三层投票

```python
votes = {}

# 1. 规则层（命中即按 W["rule"] 计票）
rule_cat = rules.match(app, url, title)
if rule_cat:
    add_vote(votes, rule_cat, W["rule"])

# 2. VLM 建议（票重 = 置信度 × W["vlm"]）
if vlm_pred:
    add_vote(votes, vlm_pred.category, vlm_pred.confidence * W["vlm"])

# 3. KNN 邻居（票重 = 距离衰减 × 来源权重 × confirm_weight）
for nb in knn_neighbors:
    w = exp(-alpha * nb.distance) * W[nb.source] * nb.confirm_weight
    add_vote(votes, nb.category, w)
```

- 距离衰减：`_decay(distance, alpha=1.0) = exp(-alpha * distance)`
- 三层互不互斥，同一类别从多个信号累加票数
- **空票兜底**：三个信号都没产出时返回 `("uncategorized", 0.0, {"signals": {}})`

### 最终置信度

```python
confidence = top1_score / (top1_score + top2_score)
```

简单可解释的 softmax 近似——top1 == top2 时为 0.5（完全不确定），只有单一类别时为 1.0。

---

## KNN 邻居从哪来（knn_neighbors 来源）

`decide_category` 把 `knn_neighbors: list[KnnNeighbor]` 当**入参**，引擎自己不查库——构造这批邻居是调用方的责任。两条候选数据通路（接线时二选一或融合，**目前都还没接**）：

- **文本向量近邻**：`analysis_results.text_embedding`（Worker 已落地的 768d float32 BLOB）+ `db.vector_search`（numpy 余弦全表扫，已实装但 search 路由暂未调用）→ 取 top-k，每个邻居的已知类别作为 `KnnNeighbor.category`，余弦距离作为 `distance`
- **视觉近邻**：pHash BK-tree（采集侧）给出汉明距离近邻

邻居的 `source` 字段决定其票重（用户确认过的邻居比纯 knn 邻居权重高），`confirm_weight` 用于进一步抬高被人工背书的样本——这正是反馈闭环的入口。

---

## decision_trace 格式

`decide_category` 返回的第三个元素，生成但**当前不持久化**（schema 有 `analysis_results.decision_trace TEXT` 列预留，接线后写入）：

```json
{
  "final_category": "学习/自习",
  "confidence": 0.82,
  "candidates": [
    {"cat": "学习/自习", "score": 3.71},
    {"cat": "娱乐/游戏", "score": 1.25}
  ],
  "signals": {
    "rule": {"hit": false, "cat": null},
    "vlm":  {"cat": "娱乐/游戏", "conf": 0.55},
    "knn":  {
      "k": 7,
      "top_votes": [
        {"cat": "学习/自习", "w": 3.1},
        {"cat": "娱乐/游戏", "w": 1.2}
      ]
    }
  }
}
```

`candidates` 取得分 top5，`signals.knn.top_votes` 取衰减后 top3，便于误判时一眼看出是哪个信号主导了决策。

---

## 规则表加载策略

`RuleSet.match` 按 `rules` 列表**顺序匹配，第一条命中即返回**，支持三种键：

```python
if rule.get("app") and rule["app"].lower() in app.lower():       return rule["category"]
if url and rule.get("domain") and rule["domain"] in url:         return rule["category"]
if rule.get("title_kw") and rule["title_kw"].lower() in title.lower(): return rule["category"]
```

- `app` / `title_kw` 大小写不敏感子串匹配；`domain` 子串匹配 URL
- 规则表期望形如（YAML 仅示意，**当前没有加载器**，`RuleSet()` 默认空表）：

```yaml
rules:
  - app: "Code"
    category: "工作/编程"
  - app: "Chrome"
    domain: "github.com"
    category: "工作/编程"
  - title_kw: "哔哩哔哩"
    category: "娱乐/视频"
```

> 规则表加载 / 配置文件 / 热加载尚未实现——`RuleSet` 是被构造好后作为入参传进 `decide_category` 的，目前生产路径不构造它。

---

## 反馈闭环（设计，Phase 2）

```
用户在 UI 修改类别
      │
      ▼
feedback 记录 (record_id, old_cat, new_cat, source=user_edit, ts)
      │
      ├──► 更新该样本 category_final（来源记在 decision_trace，无独立 category_source 列）
      │
      └──► 作为高权重邻居（user_edit=5.0 / user_confirm=3.0）回灌 KNN 投票
```

**冷启动**：规则表兜底，用户每次修改都在为 KNN 库积累强样本，分类准确率随使用提升。权重表里 `user_edit` / `user_confirm` 远高于 `vlm` / `knn`，正是为了让人工背书一票压制模型/数据的弱信号。当前 `feedback` 路由（[server/api/routes/feedback.py](../../src/timetrace/server/api/routes/feedback.py)）已存在但闭环到 KNN 库的回灌尚未接线。

---

## 接线 TODO（Phase 2）

| 待办 | 说明 |
|------|------|
| Worker 调 `decide_category` | `vlm_done` 后构造 `VlmPrediction` + KNN 邻居 + `RuleSet`，写 `category_final` / `category_suggested` / `decision_trace`（schema 无 `category_source` 列，来源记在 trace 内） |
| VLM 类别预测 | 当前 VLM 只产描述文本；需让模型额外产出 `category` + `confidence` 才能填 `VlmPrediction` |
| KNN 邻居查询 | 接 `db.vector_search`（文本向量）/ pHash BK-tree（视觉），构造 `list[KnnNeighbor]` |
| 规则表加载器 | YAML / JSON 配置 + 热加载，填充 `RuleSet.rules` |
| 反馈回灌 | feedback 路由 → 更新样本 + 作为高权重邻居参与后续投票 |

---

## 相关文档

- [分类引擎设计：权重 / 置信度 / 学习闭环](../engineering/classification.md)
- [Analysis Worker（未来的分类触发点）](analysis-worker.md)
- [存储 Schema（analysis_results / feedback）](../storage/schema.md)
