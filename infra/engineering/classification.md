# 分类算法与 KNN 权重

> 返回 [Wiki 首页](../readme.md)

---

## 来源权重配置

```python
SOURCE_WEIGHTS = {
    "user_edit":    5.0,   # 用户主动修改（最强信号）
    "user_confirm": 3.0,   # 用户点击确认
    "rule":         2.0,   # 规则匹配命中
    "vlm":          1.5,   # VLM 建议（× 模型置信度）
    "knn":          1.0,   # KNN 邻居投票（基准）
}
```

**设计意图**：
- `user_edit > user_confirm`：主动纠错比被动确认更有信息量
- `rule > vlm`：规则是人工编写的确定性知识，优先于模型猜测
- `vlm × confidence`：模型置信度低时自动降权，避免低质预测污染投票

---

## 距离衰减函数

```python
def decay(distance: float, alpha: float = 1.0) -> float:
    return math.exp(-alpha * distance)
```

- `distance = 0`（完全相似）→ `decay = 1.0`（满权重）
- `distance = 1.0` → `decay ≈ 0.37`
- `distance = 3.0` → `decay ≈ 0.05`（几乎不影响投票）

`alpha` 可按 embedding 空间的分布特性调整。

---

## 最终置信度计算

```python
# top1 / (top1 + top2) —— 简单可解释版本
confidence = top1_score / (top1_score + top2_score)
```

- 两候选分数接近时 confidence 趋近 0.5（不确定）
- 只有一个候选时 confidence = 1.0
- 等价于二分 softmax，便于 UI 可视化

---

## decision_trace 完整格式

每条 `analysis_results` 记录对应一个 decision_trace JSON：

```json
{
  "final_category": "工作/编程",
  "confidence": 0.87,
  "candidates": [
    {"cat": "工作/编程",  "score": 4.20},
    {"cat": "学习/自习",  "score": 0.63},
    {"cat": "娱乐/游戏",  "score": 0.12}
  ],
  "signals": {
    "rule": {
      "hit": true,
      "cat": "工作/编程"
    },
    "vlm": {
      "cat": "工作/编程",
      "conf": 0.72
    },
    "knn": {
      "k": 5,
      "top_votes": [
        {"cat": "工作/编程", "w": 0.91},
        {"cat": "学习/自习", "w": 0.40}
      ]
    }
  }
}
```

---

## 调参流程

1. 收集误判案例（`category_final ≠ 用户期望`）
2. 查看 `decision_trace.signals` 定位哪个信号出错
3. 调整对应权重（`SOURCE_WEIGHTS`）或补充规则
4. 重新对历史误判记录执行分类（dry-run 模式）
5. 确认后标记 `category_final` 更新，触发 `feedback` 写入

---

## 相关文档

- [Rule / Feedback Engine（实现细节）](../architecture/rule-engine.md)
- [Analysis Worker（触发分类）](../architecture/analysis-worker.md)
- [存储 Schema（analysis_results 表）](../storage/schema.md)
