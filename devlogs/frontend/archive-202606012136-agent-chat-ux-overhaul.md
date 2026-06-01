# Agent 聊天页 UX 大改：Markdown 表格/mermaid + thinking 流 + 文本块拆分 + 多会话持久化 + token 面板

**日期：** 2026-06-01
**目标：** 按用户一批「前端优化」诉求重做 Web Agent 聊天页，并补齐其依赖的少量后端协议改动（reasoning 流 + token usage）。

---

## 背景

用户给了一张聊天截图 + 一段诉求清单（7 项），核心是把 Agent 聊天页做成主流 LLM web 界面的样子：

1. Markdown 支持**表格**和 **mermaid** 渲染（截图里表格 `|---|` 裸奔）
2. 每条消息显示 **input/output token 数**（灰白色小字）
3. **thinking 流式文本**（"怎么还是没有 thinking？"）
4. 工具调用之间的 **text 块**要显示
5. 多个 text 块**拆成多条 message 气泡**，保持先后顺序
6. **消息持久化 + 多 session 列表**（刷新不丢）
7. 左侧 **token 消耗面板**（统计 llm/vlm/agent，分 input/output，工具调用次数）

**多 agent 协作上下文（关键约束）**：本仓是多 agent 共享工作树。Web Agent 聊天 + 看板的后端（`server/agent/runner.py`、`/v1/agent/chat`）是**另一个 agent 写的，且当时仍在动**（`c8dfb13 runner 改真流式` 刚落，工作树里还有它未 push 的本地提交 `09a92c0`）。本次几项诉求（thinking 流、token usage）必须改这个热文件。开工前用 AskUserQuestion 确认分工，用户选「**我全权接手（含后端小改）**」。

---

## 操作步骤

### 1. 投石问路（read-only）

- `git fetch` + log/status：本地 HEAD `09a92c0`（capture 修复，**未 push**、不是我的），origin `c8dfb13`，本地领先 1；工作树另有 `tt-conn-test.py` 散件 → 判定**另一个 agent 正在活动**。
- 用 Explore agent 测绘后端 agent 栈，得到关键事实（file:line 落实）：
  - SSE 事件只有 `token / step(tool_call|tool_result) / done / error`。
  - `reasoning_content` **已 capture 进 `reasoning_parts`，但只在「无正文」时兜底**，平时丢弃 → 所以看不到 thinking。
  - 工具间文本**其实每个 delta 都单独 emit 了 token**——是前端把所有 token 拼进一个 `content`（`AgentPage.tsx` 旧版）才挤成一个气泡。
  - **完全没取 `usage`**；**没有 chat 持久化表**；**没有 token 用量表**。
- 重读当前前端（旧读已过期，`53c2e40` 加了零依赖 `Markdown.tsx`、`c8dfb13` 改了 runner）。

### 2. 三档分类 + 拍板分工

把 7 项分成：纯前端（表格/mermaid、文本块拆分、会话持久化）/ 要动 runner（thinking、usage 小改）/ 偏重（token 面板全量统计需后端表）。AskUserQuestion 确认后端归属 → 用户「全权接手含后端小改」。

### 3. 后端：runner.py 加 reasoning 事件 + usage（`8501438`）

- 每个 `reasoning_content` delta 单独 `yield {"type":"reasoning","text":..}`（保留原「无正文兜底转答案」逻辑）。
- 加 `stream_options={"include_usage": True}`，新增 `_accumulate_usage()` 把流末 usage chunk 累加（**跨工具循环多次 LLM 调用求和**，对缺失/None 容错）；两个 `done` 事件都带 `usage{prompt/completion/total}`。
- 报告生成器 `generator.py` 复用 `runner.run()` 但只转发 token/step/error、丢弃 done → **不受影响，零改动**。
- 加 3 测试（reasoning 事件 / usage 入 done / 跨迭代求和）。`uv run pytest` 全量 **426 passed**，ruff 干净。

### 4. 前端：Markdown 换 react-markdown + remark-gfm + 懒加载 mermaid

`npm install react-markdown remark-gfm mermaid`（react-markdown 10 / remark-gfm 4 / mermaid 11）。重写 `Markdown.tsx`：
- `components` 映射每个元素到主题 token；GFM 表格用 `<div overflowX:auto>` 包裹支持移动端横滑。
- ` ```mermaid ` 走 `MermaidBlock`：`import('mermaid')` 动态导入（独立 chunk）、`securityLevel:'strict'`、render 失败/流式未闭合时回退显示源码。
- 无 `rehype-raw` → 原始 HTML 被转义，**XSS 安全**（与旧零依赖版同等保证）。保留 `{text, trailing}` 签名，AgentPage 不破。

### 5. 前端：有序 block 消息模型 + 会话存储 + 面板

- `agentApi.ts`：AgentEvent 加 `reasoning` + done 的 `usage?`。
- 新 `agentSessions.ts`：localStorage 会话存储；**助手回合 = 按到达顺序的 block 列表**（`thinking | text | tool`）；`statsForSessions` 做 token 累计。
- 新 `TurnView.tsx`：thinking 折叠块（流式自动展开）、text 独立气泡（流式光标只挂最后一个 text block）、tool chip 穿插、底部 usage 灰字。
- 新 `AgentSidebar.tsx`：会话列表（新建/切换/删除/自动标题）+ token 消耗面板（诚实标注「仅 Agent 对话；VLM/全量待后端」）。
- 重写 `AgentPage.tsx`：流式事件 fold 进 block 列表；多会话状态 + **防抖 400ms 持久化**；桌面左栏 / 移动 Radix 抽屉。

### 6. 对抗审计（workflow）+ 修复

4 路并行 lens（streaming-state / markdown-mermaid / backend-runner / contract-a11y）→ 对每个 high/medium finding 再派 verifier 复核。确认并修：
- **O(n²) Markdown 重解析**（每个 token 重渲染所有历史回合）→ `React.memo` 包 `TurnView` + `TextBubble`（完成回合在 `patchTurn` 里保持对象引用 → 跳过重解析）。
- **会话行不可键盘操作**（clickable div）→ 补 `role="button" tabIndex onKeyDown`（项目别处 CategoryFilter/TimelineCanvas 都守这条线）。
- **空草稿堆积** → `onNew` 先清空草稿。
- **卸载丢最后一轮**（400ms 防抖窗口内切页）→ 加 unmount flush（`sessionsRef` + 卸载时 `saveSessions`）。
- 不改：后端 reasoning「双发」（verifier 降级 LOW——是刻意兜底、思考块会自动收起、删了会回归空答案）；mermaid seq 计数器（每次 render 新 id 反而比稳定 ref 更安全）。

### 7. 提交 + 推送 + 部署

- 2 提交（`8501438` 后端、`5a0afd9` 前端），精确路径暂存（**绝不 `-A`**，`frontend-dist` 已 gitignore，`tt-conn-test.py`/lock 不碰）。
- AskUserQuestion 确认：推（会连带 `09a92c0`）+ 部署范围 → 用户「一起 push」+「前后端都部署」。
- 守护式 ff push（`HEAD~3 == origin == c8dfb13` 才推）。**首推遇 `Could not read from remote` 瞬断，重试即过**（`c8dfb13..5a0afd9`）。
- 后端：`gh workflow run deploy.yml --ref feature/refactor-split` → run **success（19s）**，小主机到 `5a0afd9`。
- 前端：见下「问题」——VPS 链路抽风，改 tarball 单流 + 幂等交换，最终 `index-CDkud-Te.js` 上线。
- 线上验证：`/`200 · healthz 200 · `/v1/agent/chat` GET→405 · `/v1/reports/latest` 无 cookie→401。

---

## 遇到的问题与解决

### 问题1：后端 runner 是另一个活跃 agent 的热文件

**现象：** 几项诉求要改 `runner.py`，而它刚被改过、旁边还有未 push 的 `09a92c0`。
**解决：** 不擅自动手——AskUserQuestion 确认归属（用户授权全权接手）；后端改动控制到**只动 runner.py + 它的测试**（generator.py 复用但丢弃 done，零改动），最小化碰撞面。

### 问题2：流式时 O(n²) Markdown 重解析

**现象：** 每个 SSE token → `setSessions` → 所有历史回合的 `<ReactMarkdown>` 全量重解析。
**原因：** react-markdown 每次 render 重跑 remark；完成回合无 memo。
**解决：** `React.memo(TurnView)` + `React.memo(TextBubble)`；`patchTurn` 只替换最后一个回合对象、其余保持引用 → memo 跳过。只有正在流的那个 block 重解析（不可避免，但单块很短）。

### 问题3：部署时 VPS 链路抽风 + mermaid 让产物涨到 97 文件

**现象：** `scp -r frontend-dist/.` 反复 `Connection closed / timed out`；小 `echo` ssh 能过、大传输必断。
**原因：** mermaid 懒加载把 `frontend-dist` 拆成 **97 个文件**，`scp -r` 多文件多往返在抖动链路上扛不住。
**解决：** 改 **单个 tarball 流**（`tar -czf` → scp 单文件 → 远端 `tar -xzf` 到 staging → 原子 `mv` 交换）；scp 加 **size 校验重试循环**；交换做成**幂等**（开头检测半交换、curl 校验 live bundle==目标 才停）。**用 curl 走 HTTPS 验证站点（绕开抽风的 ssh 控制通道）**。全程未中断线上。

### 问题4：多 agent 同时往同一 VPS webroot 部署前端（撞车）

**现象：** 我部署前，线上 live bundle 是 `index-D1nr0kka.js`——**既非我新构建也非上次的**，是另一个 agent 并发部署的产物（不含本次改动）。
**解决：** 按用户指示用我的 `index-CDkud-Te.js` 盖上线。**已向用户标红：两个 agent 会互相覆盖，前端上线归属需协调**。（根据当前会话推断 D1nr0kka 来自更早的 commit 构建，因 vite 内容 hash 对同源确定，hash 不同即源不同。）

---

## 知识清单

- **后端 SSE 取 usage**：OpenAI 流式要拿 token 用量必须传 `stream_options={"include_usage": True}`，服务器在流末发一个 `choices` 空、带 `usage` 的 chunk；要对「不发 usage」容错（best-effort）。多次 LLM 调用（工具循环）的用量要自己累加。
- **reasoning_content** 是 thinking 模型把思考放的字段；要流式展示就每 delta 单独发事件，别只兜底。
- **前端有序 block 流式模型**：把助手回合建模成 `(thinking|text|tool)[]` 按到达顺序追加——`token` 追到末尾 text（非 text 则新开）、`tool_call` push 工具块、`tool_result` 回填最近未填的同名工具块。这样工具间文本天然拆成独立气泡。
- **react-markdown 流式 O(n²) 陷阱**：每 token 重 render 会重解析全部历史；用 `React.memo` + 在 setState 里**保持未变对象的引用**来短路。
- **mermaid 懒加载**：`import('mermaid')` 动态导入让它（+ d3/cytoscape/katex 等，本次拆出 ~90 个 chunk）落到按需 chunk，不进首屏；`securityLevel:'strict'` + 渲染失败回退源码应对流式未闭合。
- **localStorage 持久化**：防抖写（避免每 token 写盘）+ **卸载时 flush**（防抖窗口内切路由会丢最后状态）。
- **抖动链路传多文件**：`scp -r` 多文件多往返脆；**打成单 tarball 传** + size 校验 + 幂等原子交换最稳；**站点存活用 curl 走 HTTPS 验证，绕开 ssh 控制通道**。
- **多 agent 共享 VPS webroot**：两个 agent 都手动部署前端会互相覆盖——需明确归属。
- 瞬断重试：`git push` / `scp` 偶发 `Could not read from remote` 多为瞬断，重试即过。

---

## 待办 / 遗留

- [ ] **token 面板目前是 lite 版**（仅前端按 `done.usage` 累计 Agent 对话）；全量 llm/vlm/agent 分类统计需后端用量表 + VLM worker 埋点 + 聚合接口（UI 已诚实标注）。
- [ ] **多 agent 前端部署归属**需和用户/另一 agent 协调，避免来回覆盖 VPS webroot。
- [ ] thinking + 每条 token 用量**依赖小主机 VLM 在线**（thinking 模型）；后端已重部到 `5a0afd9`，但 VLM 没 `lms load` 时聊天仍出不了字（纯前端的表格/会话/拆分/面板立即可见）。
- [ ] 手动重命名会话（目前仅首条消息自动标题）——未做。
- [ ] reasoning「双发」在「只有思考无正文」的罕见分支会让同段文字既进思考块又进答案（已收起，cosmetic，未改）。
- 已完成：2 提交 push、前后端均部署验证（后端 deploy.yml success、前端 `index-CDkud-Te.js` 上线、agent/reports 路由 405/401 正常）。
