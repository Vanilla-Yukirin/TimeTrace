# 2026-10 Owner Review 执行归档（W1–W6）

**日期：** 2026-10-22
**范围：** [`infra/PLAN-OWNER-REVIEW-2026-10.md`](../../infra/PLAN-OWNER-REVIEW-2026-10.md) 的 W1–W6，全部合入 `main`；**未推进 `deploy`、未部署、未触碰生产数据**。
**验证：** 每个 PR 的 CI（Windows lint+test+客户端打包、frontend lint+build、Docker 镜像冒烟）全绿；Linux 本地后端全量 652 passed / 7 skipped（Windows-only 采集测试由 CI 覆盖）。前端没有单测，也没有做浏览器实测。

## 背景

Owner 提出 5 个方向：①GPU/CPU 高、打开卡；②图标有版权需更换（只列 TODO）；③前端交互问题；④多设备处理差，当天多设备并行导致摘要变差；⑤搜索/检索/Agent/摘要整体待完善（无法提供真实数据，只能按架构调整）。另定时区契约：UTC 存储、查询携带时区、前端按浏览器时区显示。

## 交付

| PR | 工作包 | 要点 |
|---|---|---|
| #14 | 文档 | review 根因、执行顺序、图标 TODO、时区决策 |
| #15 | W2 多设备叙述过渡 | 叶子上下文按设备分组带名称、按设备平分截断预算、prompt 去「这台电脑」、`device_ms` + 墙钟、父窗读子窗 key_points；叶子 hash 仅非空设备追加后缀（历史不重叙述） |
| #16 | W1 前端性能 | 1s 时钟下沉叶子组件、Canvas memo + 二分裁剪/命中 + rAF 合并、tooltip 直写 DOM、历史日不轮询、樱花默认关闭、去 fixed 背景与 backdrop blur、全局 transition 仅在主题切换瞬间 |
| #17 | W3 时区契约 | `server/tz.py` 纯 ASGI 中间件（`X-Timezone`/`?tz=`，非法 400），回退 `TIMETRACE_TZ`→进程本地；Agent/MCP/`/v1/summaries` 按视图时区；金字塔只按服务端时区切；前端全请求携带浏览器时区；加 `tzdata` |
| #18 | W4 统一检索与 Agent | `retrieve_records`（FTS/LIKE + embedding，RRF，范围过滤在候选阶段）供 REST/Agent/MCP 共用；`search_summaries` 走 FTS；breakdown 支持 period；Agent「先摘要后下钻」prompt；报告预取统计 + 叙述；MCP `ask_agent` 复用工具循环；图搜语义通道改 embedding |
| #19 | W5 后端设备闭环 | `GET /v1/devices`；文本/图搜 `device_id`；Agent/MCP `list_devices` + `device_id`、`seconds_by_device`、设备描述进 prompt；MCP 补 `query_stats` |
| #20 | W5 前端 | 多设备泳道时间轴、设备选择器与 badge、网页搜索改走 `/v1/search/text`、←/→/Esc、选中自动定位、筛选/搜索状态进 URL、头部时长改墙钟、设置页设备管理 |
| #21 | W6 设备级金字塔 v2 | `device_summaries`(+FTS)、按设备级联、`DevicePyramidNarrator`（设备行 LLM 叙述 + round-robin，全局行由设备叙述拼装不再调用 LLM，无设备行历史回退）、读侧 `device_id` |

## 关键决策

- **全局摘要不再由 LLM 混写多设备**：全局行改为设备叙述的确定性拼装。既消除「把两台机器交错当成频繁切换」的虚假故事，也避免单卡 GPU 上为全局再跑一遍 LLM。
- **source_hash 兼容**：W2 只在 `device_id` 非空时追加设备后缀，历史 NULL 行哈希不变，上线不会触发全量重叙述；W6 设备表独立，不改旧表 schema，旧镜像回滚安全。
- **时区**：存储与派生金字塔保持服务端时区；只有「查询边界与给人/LLM 看的时刻」跟随请求时区。没有写死任何偏移。
- **检索单一路径**：`retrieval.py` 是 REST、Agent、MCP 唯一的搜索实现；embedder 由调用方注入，不从模型参数取。

## 过程中的偏差与纠正

- W2 提交时误把子代理 worktree（`.claude/worktrees/…`）作为嵌入仓库加入索引，随即 `git rm --cached` + amend 重推，并把该目录加入 `.git/info/exclude`。
- 写测试时有三处断言写错：DST 日界、全数字 UUID 大写化、未知设备叙述被误当作全局 LLM 输出。逐一核对后确认代码行为正确，改的是测试本身。

## 未完成 / 需要 owner 的事项

- **部署**：本轮不推进 `deploy`。发布时按标准流程 `git push origin main:deploy` → 部署机 `timetrace-update`。
- **历史设备金字塔**：上线后需对历史范围执行 `timetrace-server backfill <start> <end>` + `narrate <start> <end>`（写生产库，需单独授权与备份）。当天的摘要可以用 `narrate <today> <tomorrow> --force` 重叙述。
- **图标替换**：清单见 review 文档 §2，等 owner 方案。
- **仍是技术债**：服务端单连接 + 全局锁的读写串行（可加只读连接）；图搜未复用 `VLMHealthGate`（PLAN P1-5）；2 字中文关键词仍为 LIKE（语义通道已部分弥补）；设置页暂无「恢复启用设备」（后端无对应接口）。
