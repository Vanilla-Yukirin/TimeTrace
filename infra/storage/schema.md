# 数据库 Schema

> 返回 [存储总览](overview.md) | [Wiki 首页](../readme.md)

实现文件：`src/timetrace/storage/database.py`（`_SCHEMA` 变量）

---

## 表关系

```
records ─────────────────────────────┐
  │                                  │
  ├─ screenshots (record_id FK)      │
  ├─ analysis_results (record_id PK/FK) │
  ├─ record_tags (record_id FK)      │
  └─ feedback (record_id FK)         │
                                     │
tags ─── record_tags (tag_id FK) ───┘
categories (self-referencing parent_id)
settings (key-value 配置)
```

---

## records — 核心事件表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `ts_start` | INTEGER NOT NULL | 事件开始时间戳（epoch ms） |
| `ts_end` | INTEGER | 事件结束时间（可空） |
| `event_type` | TEXT | `window_switch` / `heartbeat` / `idle_start` / `idle_end` |
| `app_name` | TEXT | 应用名（如 "Code"） |
| `process_name` | TEXT | 进程名（如 "Code.exe"） |
| `window_title` | TEXT | 窗口标题 |
| `url` | TEXT | URL（浏览器可用，其余为 NULL） |
| `capture_reason` | TEXT | `switch` / `heartbeat` / `manual` |
| `status` | TEXT | `captured` / `pending_vlm` / `vlm_done` / ... |
| `created_at` | INTEGER | 创建时间（epoch ms） |
| `updated_at` | INTEGER | 最后更新时间 |

**索引**：
```sql
idx_records_ts_start  ON records(ts_start)
idx_records_app_ts    ON records(app_name, ts_start)
idx_records_status    ON records(status) WHERE status LIKE 'pending_%'  -- Partial Index
```

> **Phase 1 关键词检索（FTS5）**：建议在 `window_title`、`vlm_desc`（`analysis_results`）、`url` 上建 SQLite FTS5 虚表，用于关键词搜索，无需额外依赖：
> ```sql
> CREATE VIRTUAL TABLE records_fts USING fts5(window_title, url, content='records');
> ```
> Phase 1.5 向量相似检索是 FTS5 的补充，不是替代。

---

## screenshots — 截图资产表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | UUID |
| `record_id` | TEXT FK | → records.id |
| `path` | TEXT | 原图路径（相对 TimeTraceData/） |
| `thumb_path` | TEXT | 缩略图路径 |
| `width` / `height` | INTEGER | 图片尺寸 |
| `hash_sha256` | TEXT | 内容哈希（去重检测） |
| `deleted_at` | INTEGER | 软删除时间（NULL = 未删除） |
| `privacy_level` | TEXT | `normal` / `blurred` / `no_image` |
| `created_at` | INTEGER | — |

**索引**：
```sql
idx_screenshots_record  ON screenshots(record_id)
```

---

## analysis_results — 分析结果表

| 字段 | 类型 | 说明 |
|------|------|------|
| `record_id` | TEXT PK/FK | → records.id |
| `vlm_desc` | TEXT | VLM 生成描述（20–50 字） |
| `vlm_model` | TEXT | 使用的模型名 |
| `vlm_latency_ms` | INTEGER | VLM 调用耗时 |
| `category_suggested` | TEXT | 模型建议分类 |
| `category_final` | TEXT | 最终分类（规则+KNN 融合） |
| `confidence` | REAL | 置信度 0–1 |
| `decision_trace` | TEXT | JSON 格式决策追踪 |
| `error_code` | TEXT | 错误代码 |
| `error_msg` | TEXT | 错误信息 |
| `retry_count` | INTEGER | 重试次数 |
| `next_retry_at` | INTEGER | 下次重试时间（epoch ms） |
| `status` | TEXT | 状态机当前状态 |
| `locked_at` | INTEGER | Worker claim 时间（防止多 Worker 重复处理） |
| `updated_at` | INTEGER | — |

**索引**：
```sql
idx_analysis_status ON analysis_results(status)
```

---

## categories — 分类定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | — |
| `name` | TEXT UNIQUE | 分类名（如 "工作/编程"） |
| `parent_id` | TEXT FK | → categories.id（支持层级，可空） |
| `description` | TEXT | 说明 |
| `is_builtin` | INTEGER | 是否内置（0/1） |
| `is_hidden` | INTEGER | 是否隐藏（用于"不确定"等不可删除类） |

---

## tags / record_tags — 多标签

**tags**

| 字段 | 类型 |
|------|------|
| `id` | TEXT PK |
| `name` | TEXT UNIQUE |
| `description` | TEXT |
| `created_at` | INTEGER |

**record_tags**

| 字段 | 类型 |
|------|------|
| `record_id` | TEXT FK |
| `tag_id` | TEXT FK |
| `source` | TEXT（rule / vlm / user） |
| `confidence` | REAL |

---

## feedback — 用户反馈

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | — |
| `record_id` | TEXT FK | → records.id |
| `action` | TEXT | `confirm` / `edit` |
| `category_before` | TEXT | 修改前分类 |
| `category_after` | TEXT | 修改后分类 |
| `tags_before` | TEXT | JSON |
| `tags_after` | TEXT | JSON |
| `user_note` | TEXT | 备注（可空） |
| `created_at` | INTEGER | — |

---

## settings — 配置 K-V

| 字段 | 类型 |
|------|------|
| `key` | TEXT PK |
| `value_json` | TEXT |
| `updated_at` | INTEGER |

---

## 相关文档

- [存储策略总览](overview.md)
- [分析 Worker（操作 analysis_results）](../architecture/analysis-worker.md)
- [Rule Engine（读写 feedback）](../architecture/rule-engine.md)
