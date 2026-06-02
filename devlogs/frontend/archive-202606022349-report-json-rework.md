# 看板报告架构重做：从「LLM 直接写 HTML」到「LLM 产 JSON + 前端主题化模板」

**日期：** 2026-06-02
**目标：** 根治 AI 看板报告在深/浅主题下样式错乱的问题——把报告从「让本地大模型直接产 HTML」改成「模型只产结构化 JSON，颜色与排版交给前端主题化组件」；顺带修聊天页的空气泡，并排查「下午/晚上数据缺失」（实为 outbox 同步停滞）。

---

## 背景

- 接续 Agent 聊天页 UX 大改（已归档 [archive-202606012136-agent-chat-ux-overhaul.md](archive-202606012136-agent-chat-ux-overhaul.md)）之后的打磨阶段。看板（DashboardPage）此前让 LLM 直接写自包含 HTML 海报。
- **本日环境特点（贯穿全程）**：
  - 多 agent 共享同一工作树；**另一个 agent 也在并发往同一台 VPS 部署前端**（一度撞见它的 bundle `index-D1nr0kka.js` 在线）。
  - **VPS 链路持续抖动**：git push / scp / `gh` 全程间歇性 `TLS handshake timeout` / `Connection closed` / `schannel server closed abruptly`。
  - 部署铁律：后端只走 `gh workflow run deploy.yml`；前端是手动 scp 到 VPS（认可路径）；**不手动改部署机 git / 重启**（到家后可只读 ssh GTi13-Ultra 排查，仍不动手）。

---

## 操作步骤

### 1. 修聊天页「思考块和工具之间的空气泡」

**现象**：第一个「思考过程」和第一个工具调用之间出现一个没内容的空气泡。
**定位**（从 block 模型 + SSE 顺序推断，聊天不落服务端日志、对话在浏览器 localStorage）：模型在 reasoning 之后、tool_call 之前吐了一个**仅含换行/空格的 `content` delta**，runner 的 `if text:` 放行空白 → 前端 `appendText` 建出空白 text block → 渲染成空气泡。
**修复**（`frontend/src/components/agent/TurnView.tsx`）：跳过 `trim()` 后为空的 text 块；流式光标只挂最后一个**非空** text 块。commit `72dd920`，push（`7a0c20e..72dd920`，GitHub SSH 抖动，重试第 4 次成功）。**未立即重新部署**（当时因前端部署撞车先攒着）。

### 2. 排查「下午和晚上没有数据」

用户问 agent「今天在忙什么」，回答只覆盖上午 09:16–09:49，之后空白，但用户明明还在用电脑。
**只读勘察本机**（capture 客户端在 Windows，server 在小主机 Ubuntu）：
```bash
# 进程
pwsh -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | ForEach-Object { ... }"
# outbox 状态
cat /c/Users/Yuki/TimeTraceData/outbox/state.json     # {"acked": 176}
grep -c . /c/Users/Yuki/TimeTraceData/outbox/log.jsonl # 5096
cat /c/Users/Yuki/TimeTraceData/client.toml            # server.url=公网, privacy off, paused false
```
**结论**：下午/晚上活动**全被采到了**（outbox 5096 条、13:52→现在），但 `acked` 冻在 **176**，**~4920 条卡住没上传**。公网页面查的是小主机库 → 只有上午同步上去的那批。**不是 agent bug**（agent 如实读了小主机库），是**客户端→服务端同步停滞**（FIFO 停滞 / 毒丸家族，和当天上午的 outbox 污染事故同类；外加发现**多个 timetrace-client 进程**并存）。数据没丢，在本地排队。
**收尾复查**（到家后）：`{"acked":299}`、log 299 行、**pending 0**——backlog 已清空（log 已 inline 压实到最近 ~1h），4920 条全部上传成功、零丢失，恢复实时同步。进程降到 2（1 client + worker 子进程），pending 0 判定健康。

### 3. 看板深色模式不切换 + 我两次错误修法（失败路径，重点教训）

**现象**：深色页面下，看板报告卡片不跟随主题、二级文字几乎不可见。
- **错误修法 A（light paper）**：给报告外层强加浅色纸面 `#f5f6f8` + 深色基色。结果**更糟**——当次生成是「深色海报风（浅色文字）」，浅纸面让浅字彻底不可见。已部署上线（bundle `index-CMyBqsDi.js`），被用户当场打回「你改的很糟糕」。
- **错误修法 B（dark poster prompt）**：转而强制 prompt 产「不透明深色自包含海报」+ `_clean_html` 抽 ```html 围栏修思考泄漏（commit `ec3d5f7`），并 revert 掉浅纸面（`805471a`）。能用，但治标。
**根因**：让 LLM 产 HTML，它**根本不知道前端的主题 CSS 变量**，没法适配深浅；且输出深浅风格不稳定（这次浅字、下次深字），任何固定纸面都会顾此失彼。
**关键决策（不再猜）**：先**拉真实报告 HTML 看清楚**再动手：
```bash
TOKEN=$(sed -n 's/^auth_token = "\(.*\)"/\1/p' /c/Users/Yuki/TimeTraceData/client.toml)  # token 不入档
curl -sS -H "Authorization: Bearer $TOKEN" ".../v1/reports/latest?scope=recent_24h"
# 实测：模型用显式浅色文字 + 半透明背景，确认它在赌「深色页面」
```
→ 得出结论并向用户提议：**别再让 LLM 写 HTML**。

### 4. 定方案：JSON（用户拍板）

用 `AskUserQuestion` 给出 JSON vs Markdown 两个带 ASCII 预览的方案，推荐 **JSON**（报告本身有结构：大数字总览 + C 位应用 + 可选提醒 + 2–4 张洞察卡，JSON 能表达、Markdown 只能线性散文）。用户选 **JSON**。

### 5. 后端改造（`src/timetrace/server/report/generator.py`，commit `74b1997`）

- prompt 重写：**只输出一个 JSON 对象**、禁止前言/思考/围栏；字段值全中文。
- `_clean_html` → `_extract_json` + `_json_candidates` + `_parse_report`：三级兜底抽取（直接 `json.loads` → 剥 ```json 围栏 → 抠首个**配平 `{...}`**），再校验/coerce 成干净 dict；解析失败给**带友好 caveat 的兜底对象**（前端永远拿到合法结构）。落库 `fmt="json"`、`content=json.dumps(data, ensure_ascii=False)`。
- `tests/test_reports.py` 更新到 JSON 契约（含「前言+```json 围栏」抽取、跨 token 拼接）。`ruff` 绿、`pytest -k report` 10 passed。

**JSON schema（6 字段，唯一事实源 = `_parse_report`）**：
```jsonc
{
  "scope_label": "过去 24 小时",
  "headline": "约 10.5 小时",
  "headline_caption": "总活跃时长（已剔除休眠封顶）",
  "top_app": { "name": "Visual Studio Code", "value": "约 3.2 小时" }, // 或 null
  "caveat": "…还在排队分类…",  // 或 null
  "insights": [ { "emoji": "💻", "title": "…", "body": "…" } ]  // 2–4 条，后端兜底≤5
}
```

### 6. 前端改造（commit `09649d8`）

- `frontend/src/lib/agentApi.ts`：`Report.format` 加 `'json'`；新增 `ReportData` 类型 + `parseReportData()`（防御式解析，形状不对返回 `null` 让调用方兜底）。
- 新增 `frontend/src/components/dashboard/ReportView.tsx`：大数字总览卡 + C 位应用卡 + 橙色虚线 caveat 提醒 + 洞察卡片**自适应网格**，**全用主题变量**（`--bg-surface`/`--text-primary`/`--accent`/`--warning`…）→ 深浅模式完美自适应。
- `frontend/src/pages/DashboardPage.tsx`：`format==='json'` → `<ReportView>`，旧 `html` 报告仍按原样兜底渲染；生成中**不再把流式 raw JSON 怼屏上**，改「✍️ 正在落笔写报告…」。`tsc -b` + `vite build` 绿（bundle `index-z9-GQ6n0.js`）。

### 7. 部署 + 验证

- 后端 `gh workflow run deploy.yml`（抖动重试，run 26826137203 **success**）；前端 **tarball 单流 + 幂等原子交换**部署（见下）。
- **重新生成实测**：`/v1/reports/latest` 显示新报告 `format:"json"`、`headline:"约 10.5 小时"`、`top_app:{VS Code, 约 3.2 小时}`、**insights 4 条**、无泄漏 → 全链路通。
- 用户截图确认：渲染出结构化主题化海报，深浅自适应、再无看不清/不切换。

---

## 遇到的问题与解决

### 问题1：赌 LLM 写对呈现层 = 死路（核心教训）
**现象/原因/解决** 见步骤 3。一句话：**LLM 只该产数据（内容），呈现（颜色/排版）必须由知道主题变量的前端模板负责**；schema 要克制、字段少、画面我们掌控。两次「纸面」补丁都是在错误前提上打补丁，越补越糟；停下来拉真实数据看清成因后才转对方向。

### 问题2：部署链路抖动 + mermaid 让产物涨到 97 个文件
**现象**：`scp -r frontend-dist/.` 频繁中途断（`Connection closed` / timed out）。
**原因**：加了懒加载 mermaid 后 `frontend-dist` 有 97 个文件，多文件 scp 在高延迟抖动链路上扛不住。
**解决**：改 **单个 tarball 流**传输 + **size 校验** + **幂等原子交换**：
```bash
tar -czf ttfe.tgz -C frontend-dist .
# retry scp 直到远端字节数 == 本地
# 远端：mkdir staging → tar 解包 → 校验 index/js → chmod -R a+rX → mv 现网→bak → mv staging→现网（同盘 rename 原子）
```
交换脚本写成**幂等**（每轮先 `curl` 看线上 bundle hash，已是目标就跳过；半交换可从 bak 恢复），抖动断了直接重跑。

### 问题3：多 agent 并发部署前端互相覆盖
**现象**：部署前线上是另一 agent 的 bundle `index-D1nr0kka.js`。
**解决**：幂等 + curl 校验的交换循环（按目标 hash 收敛），按用户指示用我的盖上；并向用户点明「你俩 agent 会互相覆盖，需定前端上线归属」。

### 问题4：非流式 generate 在 ~60s 被切（虚惊）
**现象**：`curl POST /v1/reports/generate` 在 60s 被 `schannel server closed abruptly` 切断（http=000）。
**排查**：`/v1/reports/latest` 显示报告**已 server-side 生成并持久化**（`format:json`、内容正常）；live nginx 确认 `/v1/reports/` 与 `/v1/agent/` 都有 `proxy_read_timeout 3600s`（commit `e639a61`，未被还原）。
**结论**：60s 切断是**我的非流式 curl 在抖动隧道上空闲过久被切**，不是管道故障——**前端用的是流式端点**（SSE token 持续流动，不会空闲被切），用户不受影响。**教训：验证长生成别只看 curl 退出码，去看 `/latest` 是否落库。**

---

## 知识清单

- **LLM 产数据、不产呈现**：要深浅自适应，呈现层必须吃主题 CSS 变量；把样式交给 LLM 写死 = 必崩。schema 越克制越可控。
- **稳健抽 JSON**：模型即使被要求「只输出 JSON」仍可能裹 ```json 围栏 / 前置思考——三级兜底（直接 parse → 剥围栏 → 抠首个配平 `{}`）+ 字段 coerce + 解析失败给合法兜底对象。
- **抖动链路部署**：单个 tarball 流胜过多文件 scp；`mv` 同盘 rename 是原子的，做「staging → 换入」零停机；交换脚本写成幂等可重跑；**用 `curl`（HTTPS）验证线上状态，绕开同样抖动的 ssh 控制通道**。
- **排查 outbox 同步**：`outbox/state.json` 的 `acked` vs `log.jsonl` 行数 = 已传/总量；`acked` 长期不动 + log 增长 = 发送端停滞（毒丸 / 多 client 进程争抢）；inline compaction 会把已 ack 条目压实、log 变短。
- **用 device bearer 排查只读接口**：`client.toml` 的 `auth_token` 可 `curl -H "Authorization: Bearer …"` 打 `/v1/reports/*` 等（**token 不写进归档**）。
- **公网隧道长请求 ~60s 易被切**：服务端通常仍跑完；流式端点（持续有数据）不受影响。

---

## 待办 / 遗留

- [ ] **报告可加更丰富字段**（按需「加字段 + prompt 教填 + ReportView 加渲染」）：`categories` 占比条 / `hourly` 24 格热力带 / `tagline` 年报式金句 / `vs_previous` 同比 / insight 加 `tone` 配色。已把清单给用户待挑。
- [ ] **3h / 7d 旧 html 报告**仍走兼容兜底渲染，需点「重新生成」换成新 JSON（或等 30 分钟调度自动刷）。
- [ ] **重复 client 进程**：现为 1 client + worker 子进程、pending 0，判定健康，暂不动；若再现同步停滞，按「干净重启单实例 + 查第 ~177 条毒丸」处理（绝不删 outbox 数据）。
- 已完成并上线验证：空气泡修复（`72dd920`）、报告 JSON 化（后端 `74b1997` + 前端 `09649d8`）、outbox backlog 清空、看板主题化渲染。
