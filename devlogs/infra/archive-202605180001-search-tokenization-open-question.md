# 搜索匹配策略 —— open question（待决策）

**日期：** 2026-05-18
**状态：** 未决策。记录现状 + 候选方向 + tradeoff，等用户精力允许时讨论。

---

## 现象

首次部署后 Windows client 真实采集了 1 小时数据（约 200+ records），前端 Search 页几乎搜什么都返回"未找到符合条件的活动"。Timeline 页正常显示 → 数据是有的，是 search 没命中。

## 实测结果（直接打 API）

| 查询 | 返回 | 原因 |
|---|---|---|
| 无 q（仅 limit） | ✅ 有数据 | 全表 |
| `q=微信` | ✅ 命中 | `window_title` 字面是 "微信" |
| `q=Visual` | ✅ 命中 | `window_title` 含 "Visual Studio Code" |
| `q=vscode` | ❌ 空 | `window_title` 没 "vscode" 字串（用全称） |
| `q=VSE` | ❌ 空 | 同上，缩写不命中 |
| `q=Weixin` | ❌ 空 | `app_name` 字段没参与搜索 |
| `q=Code.exe` | ❌ 空 | `process_name` 字段没参与搜索 |

---

## 根因（两层）

### 层 1：SQL 字段覆盖窄

`server/db/sqlite.py::query_records` 当前 SQL 只 LIKE 两个字段：

```sql
r.window_title LIKE %q% OR a.vlm_desc LIKE %q%
```

但用户直觉认为可搜的字段还包括：

| 字段 | 现搜得到 | 值举例 |
|---|---|---|
| `r.window_title` | ✅ | "Review commit ... - TimeTrace - Visual Studio Code"、"微信" |
| `r.app_name` | ❌ | "Visual Studio Code"、"Weixin"、"DingTalk" |
| `r.process_name` | ❌ | "Code.exe"、"Weixin.exe" |
| `r.url` | ❌ | (浏览器才有) |
| `a.vlm_desc` | ✅（但全 NULL，VLM 未配置） | — |

### 层 2：LIKE substring 的本质限制

哪怕扩了字段，LIKE `%q%` 永远做不到：

- **缩写匹配**："VSE" → "Visual Studio Code"（首字母 / 任意词首组合）
- **中文分词**："鸣潮游戏" → "Wuthering Waves" / "鸣潮"（跨语言）
- **同义词**："聊天" → "微信 / QQ / DingTalk"

第二层问题不是改 SQL 字段能解决的。

---

## 三个候选方向

### A. 多字段 LIKE（低成本快赢）

仅扩 SQL：

```sql
r.window_title LIKE %q%
   OR r.app_name LIKE %q%
   OR r.process_name LIKE %q%
   OR r.url LIKE %q%
   OR a.vlm_desc LIKE %q%
```

| | |
|---|---|
| 工作量 | ~10 行（SQL + 1 测试 + 前端 placeholder）|
| 解决 | "Weixin" / "Code.exe" / 浏览器 URL 关键词命中 |
| 不解决 | "vscode" / "VSE" 类缩写；中文分词 |
| 风险 | OR 链让查询变慢（当前数据量无感）|

### B. FTS5 + 字符级 n-gram tokenizer

SQLite FTS5 内置 `unicode61` tokenizer 拆词，但对 CJK 不友好（无空格分词）。可改用：
- `trigram` tokenizer（SQLite 3.34+）—— 字符级 3-gram，CJK 友好
- 或自己加 `tokenize="trigram"` 创建 FTS5 表 + 触发器同步 records

| | |
|---|---|
| 工作量 | 1-2 天（schema migration + 触发器 + 索引重建 + 测试） |
| 解决 | CJK 任意子串、prefix 匹配快；BM25 排序 |
| 不解决 | "VSE" → "VSCode" 缩写（仍需 alias 表） |
| 风险 | schema 迁移要谨慎；旧数据需要 backfill 进 FTS 表 |
| 备注 | kickoff devlog 早有"切到 FTS5 MATCH"计划（`search.py::_bm25_search` docstring 写了） |

### C. 向量 embedding 语义检索

用 sentence-transformers / 类似模型把 `window_title + app_name + vlm_desc` 编码成向量，存 SQLite + faiss-style 检索。

| | |
|---|---|
| 工作量 | 1-2 周（选模型 + 编码 pipeline + 索引存储 + 检索集成 + UI 说明）|
| 解决 | 真正语义相似（"聊天" → "微信"）、跨语言、缩写（如果模型懂）|
| 不解决 | 完全精确匹配（向量召回是模糊）|
| 风险 | 模型选型、Linux 推理速度、磁盘占用、对小机器不友好 |
| 备注 | VLM `vlm_desc` 是天然的 embedding 输入源；OCR 文本也可以 |

### D. Alias 表（低技术补丁）

人工维护 `"VSCode"` → `"Visual Studio Code"` 等映射。

| | |
|---|---|
| 工作量 | ~1 天（表设计 + UI 编辑 + 查询时展开 q） |
| 解决 | 高频缩写 / 别名（用户加什么就有什么）|
| 不解决 | 长尾匹配；维护成本 |
| 风险 | 用户体验割裂："为什么这个搜得到那个搜不到" |

---

## 我的初步建议（用户决策再定）

**短期（一周内）**：方案 A，扩字段，覆盖 80% 用户直觉。**风险极低，几乎只动 1 行 SQL**。

**中期（P4 OCR 落地时一起）**：方案 B，FTS5 + trigram。理由：
- OCR 上线后 `vlm_desc` / `ocr_text` 数据量会爆增，LIKE 性能不够
- trigram CJK 友好，符合用户实际语言混搭
- 一次性 schema 迁移就到位，避免 A → B 二次重写

**长期（P5 或更晚）**：方案 C 向量检索。理由：
- 现有数据量（个人单设备）不至于让 BM25 失效
- 向量基础设施投入大，先把 trigram 跑稳再说
- VLM 语义描述本身已是某种"语义化"中间产物，叠 embedding 边际收益不明确

**方案 D Alias 表**：不主动做，但如果用户长期搜不到 X 又懒得改习惯，作为 escape hatch 兜底。

---

## 关联文件

| 文件 | 角色 |
|---|---|
| [`src/timetrace/server/db/sqlite.py:497-561`](../../src/timetrace/server/db/sqlite.py) | `query_records` 当前 SQL |
| [`src/timetrace/server/api/routes/records.py:45-82`](../../src/timetrace/server/api/routes/records.py) | `/v1/records?q=...` 路由 |
| [`src/timetrace/server/api/routes/search.py:56-138`](../../src/timetrace/server/api/routes/search.py) | `_bm25_search` + `_like_fallback`（docstring 已写 "FTS5 MATCH is the planned upgrade"）|
| [`frontend/src/hooks/useSearchQuery.ts:122-126`](../../frontend/src/hooks/useSearchQuery.ts) | 前端：无 image 时走 `/v1/records?q=`，有 image 才走 `/v1/search/by-image` |
| [`frontend/src/pages/SearchPage.tsx:131-144`](../../frontend/src/pages/SearchPage.tsx) | 搜索输入框 placeholder："关键词（窗口标题或画面描述）..." —— 实际只描述了被搜的两个字段 |

---

## 不做的决策

- 不在本次会话改任何代码（user 累，做技术决策时机不对）
- 不动 `infra/architecture/search` 类文档（架构页有 H2 deprecation 警告，整页重写延后）
- 不预判用户选哪条 —— 三条都是合理的，看后续 P4 OCR 节奏 + 实际用户体验反馈再定
