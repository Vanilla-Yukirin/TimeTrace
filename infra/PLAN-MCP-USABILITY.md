# PLAN：MCP 工具可用性修复（4 点）

**日期**：2026-06-23
**来源**：外部 bot 客户端（走 MCP 接口）实测反馈，已对真代码核实——4 点条条成立。
**状态**：✅ **已实现**（commit `6db985f`，512 tests pass、ruff clean，已 push `feature/refactor-split`、未部署）。

> 注：下面"归属与冲突"原假设有"另一个 agent"负责 `agent/tools.py`——实为同一会话（用户澄清那段对话里的"他"=本 agent）。所以 4 点由本会话统一实现，无跨 agent 冲突。spec 内容保留作实现记录。

---

## 背景

某用户用 MCP 想拉一整天的活动（当天 1400+ 条工作类记录）。`get_recent_activity` 默认 `limit=50`、上限 `200`，他设到 200 仍只拿到按时间排序的**前 200 条**（09:03–09:41），13:00 之后全被截掉。只能靠 `search_activity` 猜窗口标题关键词分段补，效率极低。

反馈 4 点，其中 **#1（limit 上限）+ #3（绝对时间窗）是刚需**——有这俩他就能一次拉全。

---

## 归属与冲突（为什么不由规划者改）

4 点全落在三个文件，**都是"金字塔/叙述层" agent 正在动或马上要动的**：

| 文件 | 本规划要改 | 那个 agent 的领域 | 冲突 |
|---|---|---|---|
| `server/agent/tools.py` | search/recent 实现 + `TOOL_SCHEMAS` | 它的 `query_stats`、时长模型 `_clamped_dur_sql`、同一个 `TOOL_SCHEMAS` 列表都在这 | ⚠️ 高 |
| `server/mcp_layer/server.py` | `@mcp.tool` 签名 + docstring | 它路线图的 "MCP folding" 切片改这个文件 | ⚠️ 高 |
| `server/db/sqlite.py` `_fts_query` | 多词搜索语义 | 它的 `summaries_fts` + `search_summaries` 共用 `_fts_query` | ⚠️ 中 |

→ 由规划者并行改 = 再次制造"两 agent 同文件互踩"。**结论：交给那个 agent 统一改，规划者只出 spec + 评审。**

---

## 4 个修复（按优先级，#1+#3 是刚需）

### ① get_recent_activity limit 上限太低
- `server/agent/tools.py` 顶部常量 `_MAX_LIMIT = 200`（约 L35）→ 抬到 **2000**（够一天 1400+ 还留余量、仍防爆 token）。`get_recent_activity` 里的 `limit = min(..., _MAX_LIMIT)` clamp 自然跟着放开。
- 同步更新 `mcp_layer/server.py` 的 docstring（「cap 200」）和 `TOOL_SCHEMAS` 里的「最大 200」描述。
- 决策：默认 **2000**；若想做"无上限 + 强制分页"也可，但默认 2000。

### ③ 加绝对时间窗 start_iso / end_iso（刚需）
- 给 `get_recent_activity` 和 `search_activity` 加可选 `start_iso` / `end_iso`（本地 `'YYYY-MM-DD HH:MM:SS'`）。
- 复用本文件**已有的** `_parse_iso_local()`（query_stats 在用）→ 解析成 `start_ms/end_ms` 直接喂 `db.query_records(start_ms, end_ms)`。
- 与 `hours_back` 的优先级：显式 iso 窗存在时用 iso，否则回退 `hours_back`。docstring / schema 同步。

### ② 分页 / 游标
- 底层 `db.query_records` **已有 `cursor` 参数**（`r.ts_start > 该 id 的 ts_start`，约 sqlite.py:708 附近）——只是 MCP 工具没透出。
- `get_recent_activity` / `search_activity` 加可选 `cursor` 入参 + 返回 `next_cursor`（取最后一条 id；不足 limit 则 null）。让客户端翻完一整天。

### ④ 中英混合多词搜不到（"judge replay history" 命中不了）
- 真因：`_fts_query`（约 sqlite.py:287）把整串当**一个连续短语**，三词必须在标题里连续出现才命中，顺序/分隔不同就漏。
- 改：含空格的多词查询 → 按空白拆 token、每个当短语再 `AND`（`"judge" AND "replay" AND "history"`）；纯中文/无空格仍当整短语（CJK 无词边界）。
- 注意：<3 字的 token trigram 命中不了，丢弃或保留由实现者判断。
- ⚠️ `_fts_query` 被 `records_fts` 和 `summaries_fts` **共用**，改完两边搜索都受影响——和 `search_summaries` 对齐着改。

---

## 验收
- 新增 MCP 工具测试：limit>200 能拿到、iso 窗过滤正确、cursor 翻到第 2 页、多词中英混合命中。
- 全测 + ruff。自包含 commit（中文 Conventional Commits，**无署名行**）。先 push `feature/refactor-split` 不部署，部署节奏由实现 agent 定。

---

## 附：其他挂起项（仅登记，不在本 plan 范围）

- **嵌入选型（已决策、暂缓执行）**：用户本意是**只用 Qwen3-VL embedding 这一个模型**（视觉 + 文本 + 联合嵌入），将来把 nomic **全部删掉**。当前 `timetrace-embserver`（独立进程、:8766、torch 跑 Qwen3-VL-Embedding-2B）**没在 box 上部署**，嵌入仍走 nomic@LMStudio:1234。现在不动——并行嵌入已开，等它把 ~1.1 万条历史 nomic 嵌入追上后再议切换（切换涉及维度变更 + 历史重嵌）。详见记忆 `project_embedding_lmstudio_shared_channel` / `project_embserver_qwenvl`。
- **指标历史 backfill**：纯 SQL、秒级、不碰 GPU，补过去 ~28 天的天/周指标行。随时能跑。
- **叙述层切片**：narrative builder(LLM) + claim 队列泛化 + source_hash 重发/巡检 + MCP folding + search_summaries。
