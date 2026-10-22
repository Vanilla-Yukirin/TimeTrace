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
4. **记录不是在线状态**：截图间隔、空描述、未完成摘要和分页的末尾都不能证明用户离线。最近活动用 `get_latest_activity`；时段总览用 `get_activity_overview`；只有要逐条查看才翻页。

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

可选再加一个 `X-Timezone: Asia/Tokyo` 这样的 IANA 时区请求头：工具里的「今天/本周」边界和返回的本地时刻都会按这个时区计算；不加时按服务端 `TIMETRACE_TZ`。存储始终是 UTC epoch ms。

## 可用工具

### 只读

- **`search_activity(query, limit=20, hours_back=None)`** — 按内容检索活动记录：关键词（窗口标题 / 应用名 / 进程名 / URL / AI 画面描述；≥3 字 FTS5 trigram + BM25，更短 LIKE）与语义相似度（文本 embedding）RRF 融合，同义说法也能命中，结果按相关度排序；另返回 `related_summaries`（叙述里提到它的时间窗）。`hours_back` 不传则全时段。**找具体内容**（某网站、某游戏、某仓库）用它。返回每条含 `id`（打标签要用）/ `ts_start_iso` / `app_name` / `window_title` / `vlm_desc` / `category`。

- **`get_latest_activity(device_id=None)`** — 每台设备最新采集记录，不需要翻页。返回采集时间、距今秒数、该记录首次入库时间、设备最后联系时间及分析状态。积压刚上传不等于刚发生；`presence` 固定为 `unknown`。没有记录的已注册设备也会列出，旧记录归入 `_unknown_device`。

- **`get_activity_overview(period="today", max_grain="day", device_id=None, limit=50)`** — **时段总览首选**：每台设备单独选择已完成摘要，父层未完成或不存在就自动补充较细层，返回窗口互不重叠。`today/this_week/this_month` 从请求时区的自然日零点开始；`custom` 需要 `start_iso/end_iso`，最大 31 天。`limit` 为每设备返回窗口上限。检查 `summary_state`、`coverage`、`truncated`，未覆盖时段不等于无活动。历史设备摘要缺失时如实标记，不混用全设备旧摘要。

- **`get_recent_activity(hours_back=24, limit=50, order="asc", cursor=None)`** — 原始记录分页，默认最早在前；`desc` 最新在前。返回 `order`、固定时间窗、`has_more`、不透明 `next_cursor`。页尾不是全局最新。翻页把游标原样传回，保持 `device_id/order`，可省略起止时间（游标已固定）；直到 `has_more=false`。新游标同时排除首屏之后新入库的记录，分析状态仍可能更新。旧 record ID 游标仍兼容读取。

- **`get_app_breakdown(period=None, hours_back=24, top_n=20)`** — 统计每个应用的累计活跃时长（秒），降序。回答 **"在 X 上花了多久"** 用它。「今天/本周/本月」传 `period=today/this_week/this_month`（按请求时区的自然日，4 点切日）；`hours_back` 只表示滚动的最近 N 小时。`get_category_stats` 同样支持 `period`。

- **`get_category_stats(hours_back=24, top_n=20)`** — 统计某时间窗内每个**分类**（AI 自动打的标签，如 `work` 工作、`entertainment` 娱乐）的累计时长（秒）。回答 **"我把时间花在哪类事情上"** 用它。还没分类的记录归入 `_unclassified`，与真正的 `uncategorized` 区分。

- **`search_summaries(grain="day", period="today", query=None, device_id=None)`** — 精确粒度查询或搜索「金字塔」叙述层（`5min→1h→6h→day→week`）。总览后的细查用它：按 `drill_down_grain` + 该窗起止（`period="custom"`）下钻。返回重叠的完整窗口，不裁剪叙述；检查 `availability=ready/pending_summary/open_window`、`window_closed` 和 `has_more`，截断时缩小范围。自动补细层请用 `get_activity_overview`。兼容旧统计查询的逻辑日切分；需要自然日边界时传明确 `custom` 起止。

- **`query_stats(metric, period="today", device_id=None)`** — 按自然时段聚合：`seconds_by_category` / `seconds_by_app` / `seconds_by_device` / `active_seconds`（墙钟并集，多设备并行只算一次）/ `record_count`。

概览出现 `unavailable` 或覆盖缺口时按 `fallback_hint` 继续。未指定设备时可用相同 custom 起止查询全局 `search_summaries`（不传 device_id），仅作全局背景；指定设备时保持筛选并分页读取原始记录，不把全局摘要归给该设备。概览截断保留最早窗口，会遗漏较新的窗口；缩小范围继续。原始分页 `window_bounds=inclusive` 保留旧边界，拼接相邻范围需按记录 ID 去重。

- **`list_devices()`** — 列出采集设备（`device_id`、名称、用户写的设备描述）。问题涉及"办公电脑""家里那台"时先用它拿到 `device_id`；`search_activity` / `get_recent_activity` / `get_app_breakdown` / `get_category_stats` / `query_stats` 都接受可选 `device_id`（`_unknown_device` = 设备身份上线前的旧记录），不传 = 全部设备。

- **`ask_agent(question, hours_back=24)`** — 让 TimeTrace 服务端**自带的本地大模型**跑一遍与 Web 聊天相同的工具循环（先读摘要、再统计/检索）后给出自然语言回答。比直接调具体工具慢（单卡多轮），只想要快速总结时用它。

### 唯一的写操作

- **`apply_label(record_id, category, note=None)`** — 给某条记录打 / 改分类标签。**这是你唯一的写权限**，不能删除或修改记录本身。
  - `record_id` 从 `search_activity` / `get_recent_activity` 返回的 `id` 字段取。
  - `category` 用分类 id（如 `work`）或中文名（如 `工作`）。
  - 内置分类（6 类，单级）：`work` 工作 · `study` 学习 · `social` 沟通 · `entertainment` 娱乐 · `system` 系统 · `uncategorized` 未分类。
  - 传未知 record_id / category 会返回 `error` 字段（不抛异常），据此自我纠正。

## 典型用法

- **"我最近在干嘛？"** → `get_latest_activity()`，按设备说明最新采集时刻和活动，不判断本人在线状态。
- **"我今天主要在干嘛？"** → `get_activity_overview(period="today")`，检查每台设备的覆盖缺口，再按需下钻或查询统计；将总览返回的明确起止传给统计工具以保持范围一致。
- **"我这周在 VSCode 上花了多久？"** → `get_app_breakdown(period="this_week")`，从结果里挑出对应应用，秒换算成小时。
- **"我有没有在摸鱼？"** → `get_category_stats(hours_back=24)`，看 `entertainment` 占比。
- **"把我下午查鸣潮攻略那几条标成娱乐"** → 先 `search_activity(query="鸣潮", hours_back=12)` 拿到各条 `id`，再对每条 `apply_label(record_id=..., category="entertainment", note="用户确认是查游戏攻略")`。
- **快速总结** → `ask_agent(question="我昨天写了多久代码")`。

## 注意

- 时区按请求头 `X-Timezone`，没传则按服务端时区；新近况、分页和摘要窗口时间带毫秒和时区偏移。下钻时原样使用返回时间，避免重复换算。旧统计工具的逻辑日切分保持兼容；不同入口对齐范围时使用明确 `custom` 起止。
- 多台设备同时在用时，按分类/应用/设备相加的时长会超过实际经过时间；"总共用了多久"用 `query_stats(metric="active_seconds")`。
- 数据不足以回答时**如实说明**，不要硬凑。
- 涉及隐私：这些是用户本人电脑上的真实活动记录，回答时尊重、克制，别外传。
