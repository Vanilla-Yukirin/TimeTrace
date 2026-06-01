---
name: timetrace
description: 查询并标注 TimeTrace 的本地活动记忆——用户电脑上记录过的窗口、截图与 AI 自动分类。当用户问"我最近/今天/这几天在干嘛""在某个应用/网站上花了多久""帮我看看时间都花哪了"，或要给某段活动打/改分类标签时使用本技能。只读 + 仅打标签，绝不删除或修改记录本身。
---

# TimeTrace 活动记忆技能

TimeTrace 是一个 **Windows-only、本地优先**的桌面活动记忆层：它低打扰地记录用户活跃窗口与关键帧截图，由本地 AI 自动给每条记录打分类标签，落地到本地 SQLite。本技能让你（一个 Claude Code / 兼容 MCP 的 agent）通过 TimeTrace 的 MCP 接口查询这些真实活动数据并据此回答，必要时帮用户微调分类标签。

## 核心原则（务必遵守）

1. **基于真实数据回答**：回答任何关于用户活动的问题前，先调用工具取真实数据，**绝不凭空编造**应用名、时长或内容。
2. **只读 + 仅打标签**：你能查询一切，但**唯一的写操作是 `apply_label`**（给某条记录改分类）。你**不能**删除记录、修改记录内容、删除截图——接口层面也没给你这些能力。这是 TimeTrace 对 AI 的硬约束："读全部，只打标签"。
3. **时长单位是秒**：工具返回的时长是秒，回答时换算成更易读的分钟 / 小时。

## 连接方式（一次性配置）

TimeTrace 的 MCP 服务通过 **streamable-HTTP** 暴露在 `/mcp/`，需要 **Bearer token** 鉴权。

### 1. 在 TimeTrace 服务端铸一个 token

在跑着 `timetrace-server` 的机器上执行：

```bash
timetrace-server tokens add claude-code      # 铸一个标签为 claude-code 的 token
# 输出形如：
#   created token 'claude-code':
#     tt_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# 复制这个 tt_live_ 开头的值，铸完需重启 server 生效
```

（`tokens list` 看已有 token（脱敏）、`tokens revoke <label>` 吊销。）

### 2. 把 MCP server 加进 Claude Code

```bash
claude mcp add --transport http timetrace \
  https://timetrace.yukirin.me/mcp/ \
  --header "Authorization: Bearer tt_live_你刚铸的token"
```

本地直连（同机或 SSH 隧道）则用 `http://127.0.0.1:8765/mcp/`。

或等价地写进 `.mcp.json`：

```json
{
  "mcpServers": {
    "timetrace": {
      "type": "http",
      "url": "https://timetrace.yukirin.me/mcp/",
      "headers": { "Authorization": "Bearer tt_live_你刚铸的token" }
    }
  }
}
```

URL 末尾的 `/` 不能省（服务端 streamable handler 挂在 mount 根）。

## 可用工具（6 个）

### 只读

- **`search_activity(query, limit=20, hours_back=None)`** — 关键词检索活动记录（窗口标题 / 应用名 / 进程名 / URL / AI 画面描述）。`query` ≥3 字走 FTS5 trigram + BM25 排序（中文友好），更短走多字段 LIKE。`hours_back` 不传则全时段。**找具体内容**（某网站、某游戏、某仓库）用它。返回每条含 `id`（打标签要用）/ `ts_start_iso` / `app_name` / `window_title` / `vlm_desc` / `category`。

- **`get_recent_activity(hours_back=24, limit=50)`** — 按时间顺序拉取最近活动快照（无关键词）。**想先总览**再决定深挖什么时用。

- **`get_app_breakdown(hours_back=24, top_n=20)`** — 统计某时间窗内每个应用的累计活跃时长（秒），降序。回答 **"在 X 上花了多久"** 用它。只统计已闭合的会话。

- **`get_category_stats(hours_back=24, top_n=20)`** — 统计某时间窗内每个**分类**（AI 自动打的标签，如 `work` 工作、`entertainment` 娱乐）的累计时长（秒）。回答 **"我把时间花在哪类事情上"** 用它。还没分类的记录归入 `uncategorized`。

- **`ask_agent(question, hours_back=24)`** — 让 TimeTrace 服务端**自带的本地大模型**取数据并直接给一句话自然语言回答（单轮，低延迟）。当你只想要个快速总结、不需要自己逐步推理时用它。

### 唯一的写操作

- **`apply_label(record_id, category, note=None)`** — 给某条记录打 / 改分类标签。**这是你唯一的写权限**，不能删除或修改记录本身。
  - `record_id` 从 `search_activity` / `get_recent_activity` 返回的 `id` 字段取。
  - `category` 用分类 id（如 `work`）或中文名（如 `工作`）。
  - 内置分类（6 类，单级）：`work` 工作 · `study` 学习 · `social` 沟通 · `entertainment` 娱乐 · `system` 系统 · `uncategorized` 未分类。
  - 传未知 record_id / category 会返回 `error` 字段（不抛异常），据此自我纠正。

## 典型用法

- **"我今天主要在干嘛？"** → `get_recent_activity(hours_back=24)` 或 `get_app_breakdown(hours_back=24)`，综合后用中文一句话总结。
- **"我这周在 VSCode 上花了多久？"** → `get_app_breakdown(hours_back=168)`，从结果里挑出对应应用，秒换算成小时。
- **"我有没有在摸鱼？"** → `get_category_stats(hours_back=24)`，看 `entertainment` 占比。
- **"把我下午查鸣潮攻略那几条标成娱乐"** → 先 `search_activity(query="鸣潮", hours_back=12)` 拿到各条 `id`，再对每条 `apply_label(record_id=..., category="entertainment", note="用户确认是查游戏攻略")`。
- **快速总结** → `ask_agent(question="我昨天写了多久代码")`。

## 注意

- 时区按服务端本地时区；ISO 时间串形如 `2026-06-01 14:30:00`。
- 数据不足以回答时**如实说明**，不要硬凑。
- 涉及隐私：这些是用户本人电脑上的真实活动记录，回答时尊重、克制，别外传。
