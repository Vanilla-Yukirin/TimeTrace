# Rule / Feedback Engine

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- **规则分类**：应用名 / 进程名 / URL 域名 / 标题关键词 → 类别映射
- **融合**：规则 + VLM 建议 + KNN 投票 → 最终 category / tags
- **反馈闭环**：用户确认/修改产生强样本，更新 KNN 原型库与权重
- **可解释性**：输出 `decision_trace` 供调试与误判排查

---

## KNN 投票算法

### 来源权重

| 来源 | 权重 |
|------|------|
| `user_edit` | 5.0 |
| `user_confirm` | 3.0 |
| `rule` | 2.0 |
| `vlm` | 1.5（× 模型置信度） |
| `knn` | 1.0 |

### 距离衰减

```python
w_dist = exp(-alpha * distance)   # alpha = 1.0 默认
# 或简化版：w_dist = 1 / (distance + eps)
```

### 最终置信度

```python
confidence = top1_score / (top1_score + top2_score)
# 等效于 softmax 近似，简单可解释
```

### 完整决策流程

```python
def decide_category(app, url, title, vlm_pred, knn_neighbors, rules):
    votes = {}

    # 1. 规则层（最高优先）
    rule_cat = rules.match(app, url, title)
    if rule_cat:
        add_vote(votes, rule_cat, W["rule"])

    # 2. VLM 建议
    if vlm_pred:
        add_vote(votes, vlm_pred.category, vlm_pred.confidence * W["vlm"])

    # 3. KNN 邻居投票
    for nb in knn_neighbors:
        w = decay(nb.distance) * W[nb.source] * nb.confirm_weight
        add_vote(votes, nb.category, w)

    # 4. 输出
    final = argmax(votes)
    confidence = top1 / (top1 + top2)
    return final, confidence, build_trace(votes, ...)
```

---

## decision_trace 格式

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

`decision_trace` 以 JSON TEXT 存入 `analysis_results.decision_trace` 字段，便于后续调参与误判分析。

---

## 规则表配置（YAML 示例）

```yaml
rules:
  - app: "Code"
    category: "工作/编程"
  - app: "Chrome"
    domain: "github.com"
    category: "工作/编程"
  - title_kw: "哔哩哔哩"
    category: "娱乐/视频"
  - app: "WeChat"
    category: "社交/通讯"
```

规则按顺序匹配，第一条命中即返回。支持 `app` / `domain` / `title_kw` 三种匹配方式。

---

## 可扩展点

| 扩展点 | 说明 |
|--------|------|
| 规则表达式 | 升级为正则匹配；支持 YAML / JSON 热加载 |
| KNN 算法 | KNN → 原型向量（prototype per label）→ 轻量线性分类器 |
| 浏览器细分 | URL 域名 + VLM 描述联合判断（Phase 2） |
| 批量反馈 | 多条记录批量确认 / 修改（Phase 2 UI） |

---

## 当前实现

`src/timetrace/rules/engine.py` — `decide_category()` 函数已实现完整的规则 + VLM + KNN 投票逻辑，包含 `decision_trace` 生成。

---

## 相关文档

- [分类权重与置信度详解](../engineering/classification.md)
- [Analysis Worker（触发分类）](analysis-worker.md)
- [相似检索层（KNN 数据来源）](../storage/vector-search.md)
- [存储 Schema（feedback 表）](../storage/schema.md)
