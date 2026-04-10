# MCP Layer

> 返回 [架构总览](overview.md) | [Wiki 首页](../readme.md)

---

## 职责

- 将 TimeTrace 能力以 MCP 工具形式暴露给外部 AI 智能体
- 强制隐私边界：**默认不返回原始截图**，只返回结构化描述 / 统计 / 摘要
- 输入输出遵循 MCP 规范与 JSON Schema

---

## 最小工具集合（Phase 1.5）

| 工具名 | 用途 |
|--------|------|
| `list_categories` | 返回所有已知活动类别 |
| `get_activity` | 按时间窗口返回结构化活动上下文 |
| `search_activity` | 文本相似检索活动记录 |
| `get_category_stats` | 返回各类别时间占比统计 |

---

## JSON Schema 示例

### get_activity

```json
{
  "name": "get_activity",
  "description": "Return structured activity context for a time window. Images are never included.",
  "input_schema": {
    "type": "object",
    "required": ["start", "end"],
    "properties": {
      "start":     {"type": "integer", "description": "Start timestamp (epoch ms)"},
      "end":       {"type": "integer", "description": "End timestamp (epoch ms)"},
      "max_items": {"type": "integer", "default": 100},
      "summary":   {"type": "boolean", "default": false}
    }
  }
}
```

### search_activity

```json
{
  "name": "search_activity",
  "description": "Text-similarity search over activity records.",
  "input_schema": {
    "type": "object",
    "required": ["query_text"],
    "properties": {
      "query_text": {"type": "string"},
      "start":      {"type": "integer"},
      "end":        {"type": "integer"},
      "top_k":      {"type": "integer", "default": 10},
      "filters":    {"type": "object"}
    }
  }
}
```

---

## 隐私边界

| 约束 | 说明 |
|------|------|
| **不返回原始截图** | 所有工具只返回文本描述 / 元数据，不包含图片 URL 或 base64 |
| **max_items 强制上限** | 超限时采用等间距采样，防止塞给模型海量原始帧 |
| **本地监听** | API 默认 `127.0.0.1`，MCP 不对外网暴露 |
| **本地 token 鉴权** | 每个 MCP 连接需要本地 token 或一次性授权码 |

---

## 当前实现

`src/timetrace/mcp_layer/tools.py` — 已实现 4 个工具函数骨架：

```python
async def list_categories(db) → dict
async def get_activity(db, start, end, max_items=100, summary=False) → dict
async def get_category_stats(db, start, end, categories=None) → dict
async def search_activity(db, query_text, start=None, end=None, top_k=10) → dict
```

`get_category_stats` 与 `search_activity` 目前返回桩数据（Phase 1.5 完整实现）。

---

## MCP 规范参考

MCP（Model Context Protocol）是开放协议，用于把外部数据源与工具接入 LLM 应用生态。TimeTrace 按 MCP 规范定义 tools 与 JSON schema，适合作为外部智能体的上下文/工具入口。

---

## 相关文档

- [Local API Server（MCP 复用同一服务层）](api-server.md)
- [隐私策略](../privacy/strategy.md)
- [开发路线图（Phase 1.5 MCP 上线）](../overview/roadmap.md)
