# 看板报告两处修复 + 分类管线深挖 + 部署卡 frp + 下一步路线图

**日期：** 2026-06-03
**目标：** 修「重新生成」网络错误与定时器只刷 24h 两个报告 bug；排查看板上"约 2177 秒还在排队补分类"的真相；摸清分类管线现状；产出后续改动路线图。**全程多为只读排查 + 少量前后端修复，未做大改。**

---

## 背景

接续本会话前段已归档的两篇：[frontend/archive-202606012136-agent-chat-ux-overhaul.md](../frontend/archive-202606012136-agent-chat-ux-overhaul.md)（Agent 聊天页 UX 重做）与 [frontend/archive-202606022349-report-json-rework.md](../frontend/archive-202606022349-report-json-rework.md)（看板报告 HTML→JSON 化）。本篇从"用户实测 demo 时发现的新问题"开始：

- 用户给了两张「定位卡」：①点「重新生成」报 `TypeError: network error`；②只有 `recent_24h` 有定时报告、`3h/7d` 永远空。要求**先深度核实根因再动手**。
- 随后用户追问看板里"约 2177 秒早期记录还在排队补分类标签"是什么、能否连服务器看、分类补标如何启动、有什么问题。
- 用户已到家，授权**直连 `ssh GTi13-Ultra`（LAN）只读排查**；并重申部署铁律不变（不手动改部署机 git/重启，部署走 deploy.yml）。
- 最后用户要求"不动代码、只讨论"下一步路线图（次日要开会）。

环境约束（沿用全程）：VPS=`ssh xcy`(103.117.123.204)，小主机=`ssh GTi13-Ultra`；公网域名 `timetrace.yukirin.me`；网络链路普遍抖动（scp/gh/git push 都要重试）。

---

## 操作步骤

### 1. 核实两张定位卡（先查再修）

**Card #2（定时器只刷 24h）——读码确认属实**：[bootstrap.py](../../src/timetrace/server/bootstrap.py) 的 `_report_scheduler` 循环只 `await gen.generate(DEFAULT_SCOPE)`。证据链对得上（小主机日志 `report.saved` 永远 `scope=recent_24h`）。

**Card #1（重新生成 network error）——卡里根因存疑，实测推翻**：卡说"clash 掐断流式 POST、请求根本没到服务器"。但流式与非流式 POST 在网络上**请求字节完全一样**（区别只在如何消费响应），clash 不可能在请求发送阶段只挡一种。于是从**同一台 clash 机器**用 curl 实测流式端点：

```bash
curl -N -sS -m 110 -X POST -H "Authorization: Bearer <REDACTED device token>" \
  -H "Content-Type: application/json" -d '{"scope":"recent_3h"}' \
  https://timetrace.yukirin.me/v1/reports/generate/stream
# → PROBE http=200 time=66.98s bytes=18504, 405 个 data: 事件
```

**结论：流式传输从 clash 机器完全跑通（200/405 事件/67s）**，根因不是"clash 挡流式 / 请求没到服务器"。真因更窄：**①浏览器读长连 HTTP/2 流在 TUN 下出问题（curl 不复现）；②链路里有个 ~60s 空闲超时**——流式因持续有 SSE 数据、连接不空闲所以扛过 67s，但**非流式 POST 静默 60s 会被掐**（后续 curl 两次都精确卡 60.4s / 61.1s）；**而服务端无论如何会跑完并落库**。

### 2. 两处修复（最小改动）

- **Card #2**：`_report_scheduler` 改成每轮遍历 `SCOPE_HOURS` 三个 scope、各自 try/except 隔离（一个失败不拖累其余）。commit `59d5d34`。
- **Card #1**：`regenerate()` 加自愈回退（[DashboardPage.tsx](../../frontend/src/pages/DashboardPage.tsx)）。先简单回退非流式（`0e1daca`）；实测发现非流式自己也会被 60s 掐，于是**加固为**：流式失败→非流式→若也被掐→**轮询 `reportsApi.latest()`（最多 ~60s、按 `created_at` 只认比本次发起更新的）捞回那份在我们被切之后才落库的报告**。commit `c5994d3`。
- 验证：`ruff` 绿、`pytest -k report` 10 passed（更新了 test_reports 到 JSON 契约的两条用例随 report-json-rework 已改）、`tsc -b`+`vite build` 绿。

### 3. 部署：前端成功，后端卡 frp

- 前端走 tarball 单流 + 幂等原子交换 + curl 校验，成功上线 `index-BSNwZd-U.js`。
- 后端 `deploy.yml` **连失败 3 次**（exit 255，10-16s），`gh run view --log-failed` 显示全卡在 `Connection closed by 121.43.33.13`——**CI-SSH 的 frp 隧道断了**，`deploy.sh` 根本没跑起来。
- 直连 LAN 只读诊断小主机：`up 1.5 周`、`timetrace-server active`、跑在 `09649d8`（已含报告 JSON 后端）、frpc 三进程都在（`2v4G.toml`/`JPVPS.toml`/`xcy.toml`）。再 grep frp 配置定位到 **CI-SSH 走的是 `2v4G.toml` 的 `ssh` 代理（`serverAddr 121.43.33.13:4070`，`:22→:10089`）**——frpc 进程在跑但这条隧道没打通，多半是 frpc 与云端 frps 的控制连接掉了没重连（API 隧道是另一条、还活着，所以网站正常）。**结论：后端 scheduler 修复 push 了但部署阻塞；非紧急（只影响未来自动刷新）。未手动重启 frpc（遵铁律）。**

### 4. 手动预填 3h/7d 报告（不依赖部署）

`generate` 端点本就支持任意 scope，于是用 device bearer 直接 server-side 触发 `recent_3h`/`recent_7d`。我的 curl 都在 60s 被掐（`http=000 time=60.4s`），但**服务端跑完落库**——稍后复查三个 scope 全 200：

```
recent_3h: json | 约 2.3 小时 | 3 insights
recent_24h: json | 约 11.5 小时 | 4 insights
recent_7d: json | 约 19.3 小时 | 4 insights   ← 在我 curl 被切之后才落库，实锤"被切但服务端照样落库"
```

### 5. 分类管线深挖（只读小主机 DB：`/home/vanilla/TimeTraceData/db/timetrace.db`）

用户问"2177 秒待补分类"是什么。查库（WAL 下只读 `mode=ro` 不锁服务）：

- **7885 条记录，5900 条 `category_final IS NULL`**（work 1322 / social 498 / system 83 / ent 48 / study 32）。4214 条是最近 24h 进来的（outbox 积压同步上来的）。
- 拆两拨：**3428 条有图**（在 VLM 队列排队，worker 实时在补，日志 `worker.vlm_done category=work` 每 ~30s 一条、2 worker，35B 单卡 ~2 条/30s → 清完要一天多）；**2474 条无图**（走 `worker.vlm_skipped_no_image` 短路）。
- **无 `analysis_tasks` 表**（与单进程笔记不同，server 端 worker 直接驱动需分析的记录）。

**软规则 vs 硬规则（关键澄清）**：读 [overrides.py](../../src/timetrace/server/settings/overrides.py) + [rules/engine.py](../../src/timetrace/server/rules/engine.py) + [worker/loop.py](../../src/timetrace/server/worker/loop.py)：`settings` 表 `app_overrides`（列名 `value_json`）每个 app 两字段——`category`(填了=**硬规则**，`decide_category` 权重 2.0 盖过 VLM 1.5) + `note`(自由文本，只**喂给 VLM 当上下文的软提示**)。读出用户真实配置：

```json
"apps": {
  "vanish": {"category": "work", "note": "ths公司内部通信软件…"},
  "claude": {"category": "work", "note": "Anthropic…AI软件，主要用于工作"}
}
```

→ 用户**确实写了硬规则**（vanish/claude 都 `category:work`），且**在生效**：Vanish work 211 / Claude work 91 就是规则打的。剩余没归位的原因（都不是规则失灵）：①Vanish social 101 = **规则之前**就被 VLM 判过的老记录（规则不回溯）；②NULL 312 有图=还在队列没轮到；③NULL 92 无图=短路不分类。

### 6. 路线图讨论（不动代码）

核心洞察：**VLM(35B) 一肩挑"描述"+"分类"两件事；分类其实不需要这么重**，是慢/堆积的根。给出三大议题 + 优先级，详见「待办」。

---

## 遇到的问题与解决

### 问题1：Card #1 根因被卡片带偏（clash 挡流式）

**现象：** 浏览器「重新生成」`TypeError: network error`，卡片断言 clash 挡流式 POST、nginx 没看到请求。
**核实：** 同机 curl 实测流式端点 200/405 事件/67s 全通 → 推翻"挡流式"。
**真因：** 浏览器长连 HTTP/2 流在 TUN 下的问题 + 链路 ~60s 空闲超时（流式因持续有数据免疫、非流式静默被掐）。
**解决：** 不纠结浏览器流式，做**自愈回退 + 轮询 latest 捞回**（服务端总会落库），对所有失败模式都稳。

### 问题2：后端部署连续失败

**现象：** `deploy.yml` 3 连败 exit 255。
**原因：** `Connection closed by 121.43.33.13`——`2v4G.toml` 的 CI-SSH frp 隧道断（frpc 在跑但与 frps 控制连接掉了），deploy.sh 没跑起来；小主机本身好的、API 隧道还活着。
**解决（部分）：** 判定为非紧急（只影响自动刷新），三份报告手动预填补齐；未手动重启 frpc（遵铁律），留给用户决定。

### 问题3：我两处先前结论被真机数据纠正（诚实记录）

- ❌ 称无图短路是"可修的 bug" → **撤回**：按用户模型"值得分类的记录=有图记录"，无图短路是有意为之。
- ❌ 称"配硬规则=跳过 VLM、省 GPU、加速积压" → **错**：读 [loop.py:220-232](../../src/timetrace/server/worker/loop.py#L220-L232) 确认 `decide_category` 在 **VLM 描述跑完之后**才应用、只覆盖结果；无图路径在它之前就 `return` 了。所以硬规则**不省算力、不加速、也不作用于无图记录**，只保证"该 app 最终分类确定"。教训重申：下结论前先撞真机数据。

---

## 知识清单

- **流式 vs 非流式 POST 的抗掐差异**：链路里的"空闲超时"只掐静默连接——SSE 流式因持续吐数据免疫，非流式静默 >60s 被掐；但**服务端生成与落库不受客户端连接被切影响**（report 照样 persist）。→ 慢生成接口的健壮前端模式 = 触发后**轮询 latest 捞结果**，而非依赖单条长连。
- **frp 多隧道独立**：API 隧道活 ≠ CI-SSH 隧道活；本项目 CI 部署走 `2v4G.toml` 的 ssh 代理（121.43.33.13:10089→:22），它掉了 deploy 全卡而网站照常。诊断：`gh run view <id> --log-failed` 看 SSH 报错 + 小主机 `pgrep -af frpc` / `~/.config/frp/*.toml` 的 `serverAddr`+proxy 映射。
- **分类管线事实**（server 端）：① `app_overrides`(settings.value_json) 的 `category`=硬规则(权重2.0)、`note`=喂 VLM 的软提示；② `decide_category` 在 VLM 之后跑、硬规则只覆盖不提速、无图记录 `vlm_skipped_no_image` 短路前置不分类；③ **规则不回溯**——改规则只影响之后处理的记录，历史 NULL/旧判不自动重刷（Vanish 规则前 social vs 规则后 work 即证）；④ VLM(35B) 同时产描述+分类，是慢的根。
- **只读查生产 SQLite**：`sqlite3.connect("file:<db>?mode=ro", uri=True)` 在 WAL 下不锁正在写的服务，安全旁路查数。
- **被切但落库**：长报告 server-side 生成即使客户端 curl 在 60s 被切，复查 `latest` 仍能拿到——可据此设计"触发+轮询"而非"长连等待"。

---

## 待办 / 遗留

- [ ] **frp 恢复后补部署 scheduler 修复**（`59d5d34` 已 push 未部署）→ 3h/7d 自动刷新。**这是唯一"做了一半"的事。**
- [ ] **frp CI-SSH 隧道（2v4G）可靠性**：会断且不自愈 → 加监控/自动重连，或既然常在家给个 LAN 直连备用部署路径。
- [ ] **链路 ~60s 空闲超时**：定位并调长（frp `tcpKeepalive`/clash/云端），让长非流式请求不被掐（自愈轮询已绕过，根治更干净）。
- [ ] **分类管线重构（路线图议题一，最高价值，待用户拍板）**：
  - 规则**回溯 sweep**（低风险）：拿现有规则重刷历史 NULL（不烧 VLM），known app + 用户 vanish/claude 历史秒归位。
  - 规则**前置短路**（有取舍）：命中硬规则就跳过 VLM。取舍=丢 `vlm_desc`/embedding（搜不到）。倾向**解耦**：轻量快路径做分类、描述按需/异步。
  - 分类换**小快模型**（4B/7B），35B 只留给描述。
  - `_unclassified` 统计**剔除无图记录**，报告不再虚报积压。
- [ ] **前端部署多 agent 互相覆盖**：定"前端谁来发"的规矩。
- [ ] 功能：`/analytics` 长范围分析页；token 消耗面板做全（需后端 usage 落库）；Agent 聊天服务端持久化（现仅 localStorage）。
- [ ] **敏感信息**：本归档已脱敏 device bearer token（client.toml `auth_token`，原值未写入）；frp 服务器 IP / ssh 别名为自有基础设施拓扑、非凭据，保留。
