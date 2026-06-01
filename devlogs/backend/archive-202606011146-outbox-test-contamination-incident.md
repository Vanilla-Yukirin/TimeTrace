# 事故归档：pytest 污染生产 outbox —— test_two_process_smoke 用了真实 outbox 目录

**日期：** 2026-06-01
**目标：** 记录一次因测试隔离缺陷 + 主 agent 反复跑全量 pytest 导致用户**正在使用的生产 outbox 被污染**的事故：客户端崩溃/卡死的排查、救数据、根因、修复全过程。**这是一篇以教训为主的归档。**

---

## 背景

用户启动 Windows 客户端（`uv run timetrace-client`）开始采集活动并上传到公网 box（`https://timetrace.yukirin.me`）。采集链路本已验证通（box 上 737→939 条记录持续增长）。

期间主 agent 为验证缩略图/看板等改动，**在后台反复跑全量 `uv run pytest`**。某次后用户的客户端开始报 `close_record failed: HTTP 404 record not found` 死循环，随后整个客户端崩溃（`OutboxError: ack_next called with no pending entries`），重启后变成卡死（队列被毒丸堵住）。

---

## 操作步骤（按时间序）

### 1. 客户端崩溃 + 一个测试失败同时出现

全量 pytest 出现 `1 failed`：`test_two_process_smoke.py::test_client_composition_matches_cli_and_records_land_on_server`，断言 "client_record_id never reached server"。同时用户客户端崩溃栈指向同一处：`OutboxSender` / `Outbox.ack_next`。

崩溃栈关键：
```
outbox_sender.py:167 await self._outbox.ack_next(entry.entry_id)
outbox.py:124 raise OutboxError("ack_next called with no pending entries")
ExceptionGroup: unhandled errors in a TaskGroup → 客户端整个退出
```

### 2. 先洗清"是不是我刚改的代码引入的"

`git stash` 掉所有缩略图/看板改动，在干净树（fdc93d7）上跑 `test_two_process_smoke` → **照样 1 failed**。**证明该失败先前就存在，不是本次改动引入。** stash pop 恢复。

### 3. 启 4-agent 诊断 workflow + 自己实测并行

启 `diagnose-outbox-crash` workflow（4 路并行：root-cause / FIFO-ordering / fix-design / test-repro + 综合）。同时主 agent 自己读 ingest 路由 + 直查 outbox 实际内容（不盲信 agent）。

**直查用户本地 outbox**（`C:\Users\Yuki\TimeTraceData\outbox`）：
```
total=261 acked=183 pending=78
HEAD idx 183: kind=close crid=3ea2fbbb  ← 卡住的毒丸
```
毒丸 payload：`{"kind":"close","client_record_id":"3ea2fbbb...","ts_end":...}`。它对应的 record（idx 178/179 的 ingest）**在 acked 范围内**（已"成功"发送过）。

### 4. 决定性证据：那些"已 acked"的 record 在 box 上根本不存在

直查 box DB（只读）：
```
3ea2fbbb by_client_record_id: None
neighbor 57db7c37: 0 record(s)
neighbor c806b25e: 0 record(s)
records_total: 939  (都有 client_record_id)
```
**idx 178-189 这些"已 acked"的 record 在公网 box 上不存在！** 但 box 有 939 条真实 record。

排除假设：查用户**本地 DB** → 也没有 `3ea2fbbb`（本地 72 条全是 `client_record_id IS NULL`，是更早单进程模式的，与 outbox 无关）。**所以不是 server-switch 问题。**

### 5. 读测试源码 → 找到铁证

```python
# test_two_process_smoke.py:65
client_cfg = ClientConfig.load_or_default(tmp_path / "client.toml")  # 文件不存在 → 全默认
# :71
outbox = Outbox(client_cfg.outbox.root_dir)   # ← root_dir 默认 = ~/TimeTraceData/outbox（真实生产 outbox！）
```
`config.py:47` `_DEFAULT_OUTBOX_DIR = Path.home()/"TimeTraceData"/"outbox"`。测试只把 `storage_cfg.data_dir` 指向 tmp_path，**唯独漏了 `outbox.root_dir`**。

### 6. workflow 综合（含自我纠错）

workflow 综合结论与实测一致，且**诚实纠正了它自己 4 个子 agent 里 2 个的错误论断**（"compact/snapshot race"不存在，有绿色测试 `test_compact_every_n_acks_triggers_after_threshold` 反证；close-404 的 by-client-id 兜底其实已存在且正确）。单一真根因 = **测试隔离失败**。这个自我纠错增强了对结论的信心。

### 7. 救数据（用户授权"跳毒丸"）

分类 pending：193 条里**只 1 颗毒丸**（孤儿 close `3ea2fbbb`），其余 192 条全是有效真实数据。

```bash
# 1. 非破坏性备份
Copy-Item outbox/state.json outbox/log.jsonl → outbox-backup-20260601/
# 2. 核实 idx 183 仍是毒丸
# 3. 原子改游标（跳过毒丸）
{"acked": 183} → {"acked": 184}   # tmp + fsync + os.replace
# 4. 全队列扫描确认无其他毒丸 → 0
```

### 8. 修测试隔离 bug（治本）

`test_two_process_smoke.py` 加一行强制隔离：
```python
client_cfg.outbox.root_dir = tmp_path / "client-outbox"  # CRITICAL: 别碰真实 ~/TimeTraceData/outbox
```
加回归测试 `test_smoke_outbox_is_isolated_from_home` 断言 outbox 在 tmp 下、不是生产默认。

**自己的 bug**：第一版回归测试断言写错（`Path.home() not in parents`——但 pytest tmp 在 `AppData/Local/Temp` 下，AppData 就在 home 底下，断言天然为假）。修正为断言"前=生产默认、后≠生产默认"。3 passed。

### 9. 清理 smoke.py 测试垃圾（用户要求"删了"）

测试往 box 注入了固定特征的假记录（`app=VSCode, window_title=smoke.py, process_name=code.exe`）。等本地真实数据排空后，精确删：
```sql
-- 按 window_title='smoke.py' AND process_name='code.exe' 精确匹配
DELETE FROM analysis_results/screenshots/records_fts/feedback/records ...
-- records 1033→1023（删10），screenshots 725→715（删10），剩余 smoke.py = 0
```

---

## 遇到的问题与解决

### 问题1（根因）：测试用了真实生产 outbox 目录

**现象：** 每跑一次全量 pytest，`test_two_process_smoke` 就读用户真实 outbox、把条目偷发给临时测试 server（用完即删）、推进 acked 游标，并往真实 outbox 塞 smoke.py 假记录。
**原因：** `ClientConfig.load_or_default(不存在的文件)` → `outbox.root_dir` 取生产默认 `~/TimeTraceData/outbox`；测试只隔离了 data_dir 漏了 outbox。
**因果链：** ingest 进了临时 DB（删）→ acked 越过这些 record → 后续 close 找不到对应 record（box 上不存在）→ 永久 404 → strict FIFO 堵死队列 → ack 游标在 race 下越界 → `OutboxError` 冒泡 TaskGroup → 客户端崩。
**解决：** 强制 `outbox.root_dir = tmp_path/"client-outbox"` + 回归测试守卫。

### 问题2：主 agent 反复跑全量 pytest（操作疏忽，是事故的触发条件）

**现象：** 主 agent 在用户正用着同一套数据时，后台多次跑全量 pytest，每次都执行那个污染测试。
**原因：** 没在第一次看到测试涉及 outbox 时就警觉它会动真实目录。
**解决（教训）：** **跑会碰文件系统的测试前，确认它们用隔离目录；尤其在用户正在用同一套数据时。** 这是主 agent 的责任，不甩给测试缺陷。

### 问题3：差点让用户误以为"数据全没了"

**现象：** 救数据后聊天 agent 问"今天上午在干嘛"答"全是 smoke.py"，用户以为真实数据全丢。
**原因：** 毒丸跳过后**最先排空的恰好是早 timestamp 的 smoke.py 测试条目**，agent 取样扎堆 smoke.py；真实 209 条排在后面没传完。
**解决：** 实测 pending 分类（339 条里真实 209、smoke.py 仅 2）纠正用户认知，**用真实数字说话不空安慰**。

---

## 知识清单

- **测试隔离 footgun**：`ClientConfig` 默认 `outbox.root_dir = ~/TimeTraceData/outbox`。任何测试 `Outbox(cfg.outbox.root_dir)` 而不显式 override，就会碰真实生产 outbox。隔离测试必须同时 pin data_dir **和** outbox.root_dir。
- **outbox 中毒模式**：孤儿 close（其 record 不在 server）会因 strict FIFO 永久 404 堵死整个队列。救法 = 备份后原子推进 `state.json` 的 acked 跳过那一条（只跳 close，不丢数据）。
- **outbox 数据安全性**：append-only + fsync + 服务端按 client_record_id 幂等 → 崩溃/重启不丢不重。备份只需 copy `state.json` + `log.jsonl`。
- **诊断并发 bug 不盲信 agent**：workflow 的 4 个子 agent 有 2 个给了看似有理（引用真实 file:line）但错误的根因；靠主 agent 直查 outbox 实际内容 + 直查 box DB 才锁定真相。**git grep / 实测数据是唯一裁判。**
- **数字说话，不空安慰**：用户以为"数据全没"时，实测 pending 分类（真实 209 vs 垃圾 2）才是负责任的回应。
- **跳毒丸的原子写**：`tmp + fsync + os.replace`，改前再核实 HEAD 仍是预期毒丸、acked 仍是预期值（防 race），改后扫全队列确认无其他毒丸。
- **精确删测试垃圾**：按 `window_title='smoke.py' AND process_name='code.exe'`（测试写死的固定特征）匹配，FK 安全顺序删（analysis_results → screenshots → records_fts → feedback → records），真实记录零误伤。

---

## 最终结果

- **数据救回**：跳过 1 颗毒丸，227 条真实 pending 正常排空到 box（records 持续增长到 1023+），用户今天活动无丢失。
- **测试隔离 bug 修复**：`test_two_process_smoke.py` pin tmp 目录 + 回归测试守卫，3 passed。以后 pytest 永不碰真实 outbox。
- **smoke.py 垃圾清理**：box 删 10 条测试记录 + 10 张截图，剩余 0，真实记录零误伤。
- **诚实复盘**：这是主 agent 责任（反复跑污染测试）+ 测试缺陷双因。教训已记。

---

## 待办 / 遗留

- [ ] 测试隔离修复 + 缩略图双轨一起 commit（用户手动 push）+ 部署。
- [ ] （可选护栏）给 `ClientConfig` 加机制：pytest 运行时若 outbox 指向真实 home 目录则报错而非静默使用，防止未来新测试再犯。
- [ ] outbox 备份 `outbox-backup-20260601/` 确认排空无误后可删。
