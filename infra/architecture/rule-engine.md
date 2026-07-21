# Rule / Feedback Engine

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

规则 / 反馈引擎（[src/timetrace/server/rules/engine.py](../../src/timetrace/server/rules/engine.py)）把两类异质信号融合成一个最终类别 + 置信度 + 可解释 trace：

- **规则分类**：应用名 / URL 域名 / 标题关键词 → 类别映射（`RuleSet.match`）
- **融合**：规则 + VLM 建议两层加权汇总（`decide_category`）
- **可解释性**：输出 `decision_trace`（JSON）供调参与误判排查
- **反馈记录**：用户确认 / 修改写 feedback 审计行（`category_before` / `category_after`）

## 引擎已接进 Worker

`decide_category()` / `RuleSet` 已有生产调用方：Analysis Worker 在 `vlm_done` 之前调用 `decide_category(app, url, title, VlmPrediction(...), RuleSet)` 并把结果 `set_category_final` 写库（[server/worker/loop.py:223](../../src/timetrace/server/worker/loop.py)）。VLM 现在产出 `category`（`payload.get("category")`），不再只产描述文本。规则来自 per-app overrides（settings KV）经 `build_ruleset()` 构造（[server/settings/overrides.py:107](../../src/timetrace/server/settings/overrides.py)）。`decision_trace` 仍生成但运行时不持久化（worker 丢弃 `_trace`，只写 `category_final`）。

---

## 数据结构

```python
@dataclass
class VlmPrediction:
    category: str
    confidence: float

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

权重写死在 `engine.py::SOURCE_WEIGHTS`，调参集中改一处。

### 两层投票

```python
votes = {}

# 1. 规则层（命中即按 W["rule"] 计票）
rule_cat = rules.match(app, url, title)
if rule_cat:
    _add_vote(votes, rule_cat, W["rule"])

# 2. VLM 建议（票重 = 置信度 × W["vlm"]）
if vlm_pred:
    _add_vote(votes, vlm_pred.category, vlm_pred.confidence * W["vlm"])
```

- 两层互不互斥，同一类别从多个信号累加票数
- **空票兜底**：两个信号都没产出时返回 `("uncategorized", 0.0, {"signals": {}})`

### 最终置信度

```python
confidence = top1_score / (top1_score + top2_score)
```

简单可解释的 softmax 近似——top1 == top2 时为 0.5（完全不确定），只有单一类别时为 1.0。

---

## decision_trace 格式

`decide_category` 返回的第三个元素，生成但**当前不持久化**（schema 有 `analysis_results.decision_trace TEXT` 列预留，接线后写入）：

```json
{
  "final_category": "study",
  "confidence": 0.82,
  "candidates": [
    {"cat": "study", "score": 3.71},
    {"cat": "entertainment", "score": 1.25}
  ],
  "signals": {
    "rule": {"hit": false, "cat": null},
    "vlm":  {"cat": "entertainment", "conf": 0.55}
  }
}
```

`candidates` 取得分 top5，便于误判时一眼看出是哪个信号主导了决策。

---

## 规则表加载策略

`RuleSet.match` 按 `rules` 列表**顺序匹配，第一条命中即返回**，支持三种键：

```python
if rule.get("app") and rule["app"].lower() in app.lower():       return rule["category"]
if url and rule.get("domain") and rule["domain"] in url:         return rule["category"]
if rule.get("title_kw") and rule["title_kw"].lower() in title.lower(): return rule["category"]
```

- `app` / `title_kw` 大小写不敏感子串匹配；`domain` 子串匹配 URL
- 规则表期望形如（YAML 仅示意当前内存形态，**没有 YAML 文件加载器**——见下方"规则来源"）：

```yaml
rules:
  - app: "Code"
    category: "work"
  - app: "Chrome"
    domain: "github.com"
    category: "work"
  - title_kw: "哔哩哔哩"
    category: "entertainment"
```

规则来源：生产路径的 `RuleSet` 来自 settings KV 的 per-app overrides（[server/settings/overrides.py](../../src/timetrace/server/settings/overrides.py)），`build_ruleset()` 把 overrides map 适配成 `RuleSet`，worker 每任务 `load_overrides` 热加载（无需重启）。独立 YAML / JSON 配置文件加载器仍未实现。

---

## 反馈边界：当前只纠正单条，不自动学习

KNN 库已删除。用户通过 UI/MCP 修改分类时，系统更新该记录的 `category_final` 并写 feedback 审计行；它不会成为近邻、规则或后续分类的训练样本。`SOURCE_WEIGHTS` 中保留的 `user_edit` / `user_confirm` 目前没有生产调用方，不能据此宣称“越用越准”。Classifier V2 如需复用人工纠错，必须重新设计污染隔离、失效与回滚，而不是恢复旧 KNN 投票。

---

## 接线状态

| 项 | 状态 |
|------|------|
| Worker 调 `decide_category` | ✅ 已实装：`vlm_done` 前构造 `VlmPrediction` + `RuleSet` 调 `decide_category`，写 `category_final`；`confidence`、`category_suggested`、`decision_trace` 与阶段时间戳已持久化，可在 `/audit` 查看 |
| VLM 类别预测 | ✅ 已实装：VLM 输出含 `category` enum（`payload.get("category")`），填进 `VlmPrediction`（[server/worker/loop.py:227](../../src/timetrace/server/worker/loop.py)） |
| 规则表构造 + 热加载 | ✅ 已实装：以 settings KV 的 per-app overrides 形态落地（非 YAML），`build_ruleset()` 构造 `RuleSet`，worker 每任务 `load_overrides` 热加载（[server/settings/overrides.py:107](../../src/timetrace/server/settings/overrides.py)） |
| 独立 YAML / JSON 规则文件加载器 | 未实现（当前规则只来自 settings KV overrides） |
| 反馈回灌投票 | 已作废：KNN 库已删除，feedback 仅做审计记录，不回灌任何投票 |

---

## 相关文档

- [分类引擎设计：权重 / 置信度 / 学习闭环](../engineering/classification.md)
- [Analysis Worker（未来的分类触发点）](analysis-worker.md)
- [存储 Schema（analysis_results / feedback）](../storage/schema.md)
