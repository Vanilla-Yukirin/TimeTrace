# Demo Sprint 主线（一）：VLM 接入 LM Studio + FTS5 搜索 + MCP 4 工具

**日期：** 2026-05-30（跨 05-27 ~ 05-30 多次会话）
**目标：** 周三 astar 周会 demo —— 本地 Claude Code 经 MCP 问"我昨天干了什么"，全链路本地优先跑通。本篇记录 demo sprint 第一波：VLM / 搜索 / MCP。

---

## 背景

停滞约 12 天后重启。用户已在小主机（GTi13-Ultra，SSH 别名 `GTi13-Ultra` 直连 / `GTi13-Ultra-2v4G` 走 FRP 隧道，在外网只能用后者）部署了 LM Studio。目标是把 PLAN.md 的 demo sprint 推完：本地 LLM + 搜索 + MCP 全链路。用户明确"不缺 token，狠狠推进"。

**模型栈**：`qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive`（35B MoE 激活 3B，原生多模态，既看图又当 agent）+ `text-embedding-nomic-embed-text-v1.5`（文本 emb）。小主机 GPU 实为 **RTX 4090 D 24GB**（早先误记为 3080）。

---

## 操作步骤

### 1. VLM 接入 LM Studio（commit 2bfb144）

`.env` 指向 `http://127.0.0.1:1234/v1`，model = qwen3.6-35b-a3b。实测踩到 LM Studio + Qwen3 两个怪癖：

- **response_format**：LM Studio 拒绝 `json_object`，报 `must be 'json_schema' or 'text'`。改用 `json_schema` 严格模式（OpenAI 2024-08+ / vLLM / llama.cpp 都支持），并把 `_DESCRIBE_SCHEMA` 内联。
- **reasoning_content 怪癖**：LM Studio 把 Qwen3+ 全部输出当 reasoning 归到 `reasoning_content` 字段、`content` 留空。试过 system `/no_think`、`chat_template_kwargs.enable_thinking=false`、`extra_body` 三种关思考方法**都不奏效**。代码兜底：`_extract_message_content()` 读 content 为空时 fallback `reasoning_content`（getattr 实现，兼容 SimpleNamespace 测试 double）。
- describe timeout 60s→120s、heartbeat 15s→30s（35B 冷加载首推理 ~60s）。

**验证**：重启 server 后 695 条积压秒级开清，每条 3-5s。质量爆表 —— 鸣潮识别"哥莱姆区域 Lv.70 守岸人团子"，微信识别"24工设2龚馨铃"群 + CameraMonitorSystem 程序讨论，钉钉识别群直播。

### 2. FTS5 trigram 搜索升级（commit 851f860）

原搜索只 LIKE `window_title + vlm_desc(全 NULL)` 两字段。升级：

- 加 `records_fts` virtual table（trigram tokenizer，CJK 友好），覆盖 5 字段：window_title / app_name / process_name / url / vlm_desc
- 迁移：冷启动检测空 FTS + 非空 records → 自动回填（幂等）
- 维护：`insert_record` 同步插入、`save_description` 同步 UPDATE vlm_desc（无触发器，debug 友好）
- 查询策略：≥3 字符 → FTS5 MATCH + BM25 排序；<3 字符 → 多字段 LIKE 兜底（"VS"/"鸣潮"仍命中）
- `_fts_query()` 用 `"…"` 包裹整词做 phrase 匹配，避开 AND/OR/NEAR/标点注入

**生产验证**（小主机真数据）：`哥莱姆`(3字CJK)→FTS5→vlm_desc 命中；`Code.exe`→FTS5→process_name（新解锁字段）；`鸣潮`(2字)→LIKE→window_title；全部返回结果。

### 3. MCP 4 工具 + 挂 /mcp（commit 0882a4c → e0ca5a4）

`uv add mcp`，新建 `server/mcp_layer/server.py`，4 个工具：

- `search_activity(query, limit, hours_back?)` — 走 query_records
- `get_recent_activity(hours_back, limit)` — 时间窗 snapshot
- `get_app_breakdown(hours_back, top_n)` — 按 app 聚合时长（COALESCE(ts_end,ts_start)）
- `ask_agent(question, hours_back)` — 把最近 N 小时活动喂本地 Qwen3，一次 LLM 调用产自然语言答案

**接线踩坑串**（4 个调试 commit）：
- `app.mount("/mcp", ...)` 子应用绕过 FastAPI Depends（已知，Phase 3 中间件解决）
- 首次 `initialize()` 报 `Session terminated` → FastMCP 的 `session_manager.run()` 必须跑在 FastAPI lifespan 里（即使 stateless_http=True）→ `0d849b9`
- `streamable_http_path` 默认 `/mcp` 跟 mount 撞 → 实际服务在 `/mcp/mcp/`。改 `streamable_http_path="/"` → `e0ca5a4`
- `stateless_http=True + json_response=True` 简化单次工具调用

**端到端验证**：真 MCP client（streamablehttp_client）→ list_tools 拿到 4 个 → `哥莱姆` 精准命中 → `get_app_breakdown` 返回 Wuthering Waves 1269s 等聚合。

### 4. LM Studio 50K context（用户决策）

用户指出 4096 太小，20G 卡能拉 100K。实测 50K 估算仅 17.3 GiB。`lms load qwen... -c 50000 --gpu max`，VRAM 17.6/20GB。ask_agent 上下文从 60×200 扩到 80×160。

**ask_agent 真 LLM 验证**：问"过去 10 天主要干什么" → 48s 返回，按时长排序分项说明（鸣潮/VSCode/oopz/钉钉），**且主动元认知**："80 条仅覆盖片段，建议结合 app_breakdown 全量统计"。

### 5. Claude Code 接入（commit e5a2c44）

- `.mcp.json` 项目级：`{"mcpServers":{"timetrace":{"type":"http","url":"http://127.0.0.1:8765/mcp/"}}}`
- `.claude/skills/timetrace-mcp.md`：4 工具决策树 + hours_back 默认建议 + 反模式（别用 ask_agent 算时长 / 别用 get_recent_activity 当 search）+ 隧道断 fail 提示

### 6. ask_agent 空窗扩库兜底（commit a837378）

数据全是 5-17 那批（12 天前），24h 窗口空。改：窗口空时 fallback 全表查询，前缀加"自动扩窗到 ~Yh"说明。**注意此版有 bug，见 [archive-...-embedding-text-pipeline](archive-202605300151-embedding-text-pipeline.md) 的 AI review HIGH-1**。

---

## 知识清单

- **LM Studio JIT 自动加载**：请求带 model 字段、未加载会自动 load（冷启动几百 ms）。`lms load <model> --ttl <秒>` 手动控制；`--ttl 99999999` ≈ 278h 显示（不是无限）。
- **LM Studio 必坑**：拒 json_object / Qwen3 输出进 reasoning_content / 关思考三法全失效 → 代码兜底比折腾配置稳。
- **FTS5 trigram** 对 CJK 友好（字符级 3-gram，无需空格分词），≥3 字符才匹配。
- **FastMCP mount 到 FastAPI**：必须 lifespan 驱动 session_manager；streamable_http_path 默认 `/mcp` 会和 mount path 叠成 `/mcp/mcp/`。
- **ask_agent 元认知**：Qwen3-35B 能主动指出"样本不足"，是 demo 高光。

---

## 待办 / 遗留

- [ ] Embedding 接线（Phase 2c）见 embedding 篇
- [ ] dress rehearsal：本机 Claude Code + .mcp.json + 3 问走一遍
- [ ] 用户亲手开 timetrace-client 攒新鲜数据（demo 需要）
