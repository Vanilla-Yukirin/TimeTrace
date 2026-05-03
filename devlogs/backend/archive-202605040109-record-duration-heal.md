# 修复异常长 Record 片段

**日期：** 2026-05-04
**目标：** 排查并修复 TimeTrace 时间轴中单条 record 异常跨越 12 小时以上的问题。

---

## 背景

本次会话先完成了 VLM prompt 的小修：`src/timetrace/vlm/client.py` 中对 `summary` / `description` 增加“不要以该截图/这张图/图中/画面中等元叙述开头”的要求，并把窗口标题提示从“仅供辅助参考，可能不准确”软化为“仅供辅助参考”。对应测试为 `tests/test_vlm.py` 中新增的 prompt 断言，用户反馈当时已有 `24 passed`。

随后回到第一个问题：数据库中出现“长达 12 小时的 record”。需要判断到底是：

- `ts_end - ts_start` 极大；
- `ts_end IS NULL` 且开始时间是很久以前；
- 还是 worker 没有切换导致状态卡住。

---

## 操作步骤

### 1. 定位项目说明与数据库

根据 `AGENTS.md` 指引读取 `CLAUDE.md`，确认数据目录：

```text
%USERPROFILE%/TimeTraceData/
```

数据库实际位置：

```text
C:\Users\Yuki\TimeTraceData\db\timetrace.db
```

期间 `rg` 在 Codex 桌面沙箱中启动失败：

```text
Program 'rg.exe' failed to run ... 拒绝访问
```

改用 PowerShell 与 `sqlite3` 只读查询完成排查。

### 2. 扫描 records 异常形态

核心统计 SQL：

```powershell
sqlite3 "$env:USERPROFILE\TimeTraceData\db\timetrace.db" "SELECT COUNT(*) AS total, SUM(ts_end IS NULL) AS open_rows, SUM(ts_end IS NOT NULL AND ts_end-ts_start>3600000) AS gt_1h, SUM(ts_end IS NOT NULL AND ts_end-ts_start>43200000) AS gt_12h, MIN(ts_start), MAX(ts_start) FROM records;"
```

当时结果：

```text
23932|1|13|6|1776532118982|1777827597429
```

结论：

- 总 records：23932；
- 当前 open records：1；
- 已闭合但 `> 1h`：13；
- 已闭合但 `> 12h`：6。

继续查看长记录：

```text
2026-04-25 03:23:13 -> 2026-04-26 22:54:00   43.51h   Unknown / unknown.exe / VS Code 标题
2026-04-27 01:54:57 -> 2026-04-28 00:00:03   22.09h   Unknown / unknown.exe / 开始
2026-04-24 00:47:06 -> 2026-04-24 22:11:45   21.41h   Unknown / unknown.exe / 开始
2026-04-19 04:14:10 -> 2026-04-19 21:43:13   17.48h   Unknown / unknown.exe / 开始
2026-04-28 22:28:36 -> 2026-04-29 11:01:49   12.55h   Unknown / unknown.exe / 开始
2026-04-28 01:58:27 -> 2026-04-28 14:05:45   12.12h   Unknown / unknown.exe / 开始
```

### 3. 区分 open orphan 与已闭合长段

查询 `ts_end IS NULL`：

```text
2026-05-04 01:00:11  age_hours=0.0  Unknown / unknown.exe / VS Code 标题
```

这说明问题不是当前有一条开了 12 小时的 NULL record。

进一步检查长段与下一条 record 的边界，发现大多数长段的 `ts_end` 正好等于下一条 `ts_start`，即历史 orphan 被 `_close_open_records_before()` 修复时桥接到了很久之后的下一条记录。

关键迹象：

```text
updated_local        n   open_rows  gt_1h  max_h
2026-04-30 12:12:41  17  0          12     43.51
```

这表明 17 条历史记录在同一启动/修复时刻被批量更新。

### 4. 确认当前版本是否仍产生长记录

查询 `2026-04-30 12:12:41` 之后新增记录：

```text
records_since_heal=1205
open_rows=1
gt_1h=0
max_minutes=0.54
```

结论：当前版本没有继续产生新的 `>1h` 长 record；问题集中在旧 orphan 被修复成跨天长段。

### 5. 修改存储层修复策略

修改文件：

- `src/timetrace/storage/database.py`
- `tests/test_storage.py`

核心策略：

```python
_MAX_ORPHAN_BRIDGE_MS = 5 * 60 * 1000
```

修复点：

1. `_close_open_records_before()` 不再无条件使用 `MIN(next.ts_start)`。
2. 只有下一条记录距离当前 orphan `<= 5 分钟` 时，才闭合到下一条 `ts_start`。
3. 如果 gap 超过 5 分钟，则设为 `ts_end = ts_start`，避免跨夜/跨天长条。
4. 新增 `_cap_implausible_record_durations()`，在 `Database.init()` 阶段把旧版本已经闭合出来的超长记录也压成零时长。

选择 5 分钟的理由：

- 默认 heartbeat 间隔是 30 秒；
- idle threshold 是 180 秒；
- 单条 record 超过 5 分钟通常不是可信活动持续时间，而是 stale boundary artifact。

### 6. 测试与修正

第一次运行存储测试时，遇到 uv 缓存目录权限问题：

```text
error: Failed to initialize cache at `D:\Temp\uv_cache`
Caused by: failed to open file `D:\Temp\uv_cache\sdists-v9\.git`: 拒绝访问。 (os error 5)
```

使用升级权限重新运行：

```powershell
uv run pytest tests/test_storage.py
```

第一次测试失败 2 条，因为旧测试把 orphan 的 `ts_start` 强制改为 `1000`，再插入当前时间的新记录。按新规则，这种超长 gap 应该压成零时长，而不是闭合到新记录。

调整旧测试：把“短 gap 可桥接”的测试改为使用最近时间戳；另新增“超长 gap 不桥接”的测试。

最终验证：

```text
tests/test_storage.py ......................  22 passed
```

全量测试：

```text
101 passed
```

### 7. 用户本地启动验证

用户重启应用：

```text
2026-05-04 01:06:10 [info] database.cap_implausible_record_durations count=17
```

这说明启动时确实清理了 17 条历史异常长 record。

随后用户执行 SQL 验证：

```powershell
sqlite3 "$env:USERPROFILE\TimeTraceData\db\timetrace.db" "SELECT COUNT(*) FROM records WHERE ts_end IS NOT NULL AND ts_end - ts_start > 3600000;"
```

结果：

```text
0
```

确认历史 `>1h` 异常片段已经被清理。

---

## 遇到的问题与解决

### 问题 1：误判风险：worker 没切还是 record 时间异常

**现象：** 用户看到长达 12 小时的 record。

**原因：** 需要区分 `analysis_worker` 状态、`ts_end IS NULL` open record、以及已闭合但 duration 极大的 record。

**解决：** 直接查 SQLite，按三类情况拆分统计。最终确认是已闭合历史 record 的 `ts_end - ts_start` 极大，不是 worker 卡住，也不是当前 open record。

### 问题 2：旧 orphan-heal 会把跨天 gap 桥接成长条

**现象：** 多条长 record 的 `ts_end` 等于下一条 `ts_start`。

**原因：** `_close_open_records_before()` 对历史 `ts_end IS NULL` 行优先使用 `MIN(next.ts_start)`，没有判断下一条记录距离是否合理。

**解决：** 加入 `_MAX_ORPHAN_BRIDGE_MS = 5 分钟`。只有短 gap 才桥接，长 gap 直接压成零时长。

### 问题 3：已经闭合的历史长条不会再被 orphan-heal 处理

**现象：** 仅修复 `ts_end IS NULL` 逻辑不能修复用户现有 DB 中已经闭合的 12h/43h 长条。

**原因：** 这些历史数据已经有 `ts_end`，不再满足 orphan-heal 的 `ts_end IS NULL` 条件。

**解决：** 在 `Database.init()` 中新增 `_cap_implausible_record_durations()`，启动时清理 `ts_end - ts_start > 5 分钟` 的已闭合异常记录。

### 问题 4：旧单测与新策略冲突

**现象：** `test_insert_closes_prior_orphan_to_next_ts_start` 和 multi-orphan 测试失败。

**原因：** 测试使用 `ts_start=1000`，与当前插入时间形成超大 gap；旧断言期待桥接到新记录，新策略正确地压成零时长。

**解决：** 把旧测试改成最近时间戳，用于验证短 gap 可桥接；新增测试覆盖超长 gap 压成零时长。

---

## 知识清单

- 对时间轴异常，优先用 SQL 区分：
  - `ts_end IS NULL` 的 open record；
  - `ts_end - ts_start` 极大的 closed record；
  - 相邻 record 的 gap 与边界是否吻合。
- `updated_at` 不能作为 record 结束时间推断依据，因为 worker/VLM 状态更新也会修改它。
- orphan 修复不能只看“下一条记录是否存在”，还要判断两条记录之间的时间距离是否可信。
- 当前采集配置下，heartbeat 30s、idle 180s，单条 record 超过 5 分钟基本可视为异常边界。
- 修复历史数据时，要同时考虑：
  - 未来写入路径；
  - 启动时 orphan 修复路径；
  - 已经被旧版本写坏的闭合数据。

---

## 待办 / 遗留

- [ ] `app_name = Unknown` 仍是独立问题，来自 `capture/window.py` 中 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 失败的 fallback；微信、QQ 等客户端可能需要管理员权限或额外启发式。
- [ ] 如后续需要保留“长时间同一窗口活动”的真实持续时间，应引入显式 idle/session 边界，而不是让单条 record 表示长跨度。
