---
name: timetrace-mcp
description: 用 TimeTrace MCP 工具回答用户关于"过去活动 / 时间分配 / 在某个应用花了多久 / 某段时间在干什么"的问题。当用户问"我今天/昨天/最近做了什么"、"在 X 应用花了多久"、"我什么时候在用 Y"、"帮我查一下我之前看过的 Z" 等类型问题时触发。
---

# TimeTrace MCP 工具速查

TimeTrace 在小主机上记录了你的桌面活动（窗口切换 + 截图 + VLM 文字描述）。
本项目 `.mcp.json` 注册了 4 个 HTTP MCP 工具，前提：本机已开 SSH 隧道
`ssh -N -L 8765:127.0.0.1:8765 GTi13-Ultra-2v4G`（隧道断 → tools 全 fail）。

## 4 个工具及调用时机

| 工具 | 何时调 | 典型参数 |
|---|---|---|
| **`search_activity`** | 用户说出具体关键词、应用名、URL、文件名片段 | `query="鸣潮"`, `limit=10`, `hours_back=72` |
| **`get_recent_activity`** | 用户问"最近/今天/这几小时在干什么"，但**没**点名具体应用 | `hours_back=24`, `limit=50` |
| **`get_app_breakdown`** | 用户问**时长统计** —— "在 X 上花了多久"、"哪个应用最耗时" | `hours_back=24`, `top_n=20` |
| **`ask_agent`** | 用户问的是**需要推理 + 整合**的开放问题，单 tool 答不全 | `question="过去一周我在哪些代码仓库花了时间"`, `hours_back=168` |

## 决策树

1. **想要时间数字？** → `get_app_breakdown`（精确到秒）
2. **想要具体记录（标题/截图描述）？** → `search_activity`（有关键词）或 `get_recent_activity`（无关键词）
3. **想要自然语言总结/排序/推理？** → `ask_agent`（最贵，~30-60s，但会自己整合）
4. **复杂问题**：先 `get_app_breakdown` 拿精确数字 + `search_activity` 拿细节 → 自己合成答案；ask_agent 只是兜底

## 时间窗 (hours_back) 默认建议

- "今天" → 24
- "昨天" → 48（留 buffer）
- "这周" → 168
- "最近" / 没说时间 → 24
- "过去一个月" → 720（cap）

## 常见反模式

- **不要** 仅靠 `ask_agent` 回答时长问题。它基于样本估算，会偏低。先 `get_app_breakdown` 拿数字再让 ask_agent 加叙述。
- **不要** 用 `get_recent_activity` 当 search 用 —— 没有关键词过滤，会拿回一大堆无关记录浪费 context。
- 隧道挂了所有 tool 都会 fail，提示用户检查 `ssh -N -L 8765:...`。

## 数据现状提醒

- 记录从 2026-05-17 开始（首次部署日）
- `vlm_desc` 字段质量很高（Qwen3-35BA3B 写的），可以直接读
- 截图本身没暴露到 MCP（隐私），只暴露文本描述
