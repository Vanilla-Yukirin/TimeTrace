# Demo Sprint 主线（二）：文本 Embedding 管线 + AI review 修复

**日期：** 2026-05-30
**目标：** 给搜索加语义向量通道（nomic 文本 emb），并修掉两轮 AI review 抓出的真 bug。与 infra agent 的登录系统并行推进、刻意避开 app.py 冲突。

---

## 背景

FTS5 只能字面匹配。用户搜"二次元"命中不了鸣潮（vlm_desc 里没这三字）。Embedding = 把 vlm_desc 编码成向量，按语义相似度检索。用户之前说"embedding 我自己搞，你们先 fallback 文本检索"，本次会话改为主 agent 接手。拆 3 阶段：1=通路、2a=回填、2b=引擎、2c=接线（避开 infra）、3=ask_agent 语义检索。

---

## 操作步骤

### 1. Phase 1 通路（commit 5fed971）

- `common/config.py::EmbeddingConfig`（base_url/api_key/model/dim=768，from_env 走 `TIMETRACE_EMBEDDING_*`，api_key/base_url 缺省回退 VLM 那套，model 不给则整体 disable）
- `server/embedding/client.py::EmbeddingClient.embed(text)` → packed float32 bytes（4×dim），dim 不符抛 EmbeddingError；`cosine_similarity` free func（零向量返 0 不 NaN）
- `db/sqlite.py`：analysis_results 加 `text_embedding BLOB` + `text_embedding_model TEXT` + 幂等 migration + `save_text_embedding()`
- `worker/loop.py`：vlm_done 后 `_embed_and_save` 最佳努力 —— 失败 log+skip，**绝不阻塞 vlm_done**（backfill 兜底）；embedding=None short-circuit
- bootstrap 注入 EmbeddingClient + 退出 aclose

测试 +13，全套 324 passed。

### 2. Phase 2a 回填 + 2b 引擎（commit 2670dac）

- worker.run() 重构：backfill 作为独立一次性任务跟 consume loop 并行 gather；VLM 关闭也跑（emb 独立于 VLM）
- `_backfill_embeddings`：批量(32)扫 vlm_desc 非空但 emb NULL；**双失败模式** —— 单行 poison 加入 skipped 集合跳过续跑（不让队头毒行饿死后面）；连续 5 次失败判定端点 down 整体 abort
- `db.vector_search(query_vec, limit, start/end_ms)`：numpy 余弦全表扫（数百~数千行×768 维 <10ms），dim 不符跳过，时间窗过滤
- `server/retrieval.py::reciprocal_rank_fusion`：RRF 融合（k=60 + 可选权重 + id-asc 确定性 tie-break）

### 3. 生产验证（小主机真数据）

部署后 `.env` 追加 `TIMETRACE_EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5`，重启。backfill 日志：**533 条全部 embed，17 秒跑完**（nomic JIT 自动加载）。

语义检索冒烟（FTS5 做不到的）：
```
[二次元游戏角色]  0.701 | Wuthering Waves | 鸣潮角色选择界面...
[写代码调试程序]  0.617 | Visual Studio Code
[和朋友网上聊天]  0.564 | oopz 语音聊天
```
**"二次元"三字不在任何 vlm_desc 里，向量照样命中鸣潮** —— 语义 vs 字面的核心差异，demo 高光。

---

## 遇到的问题与解决

### 问题1：AI review 抓出 2 个真 bug（commit 5bfb5ee）

**HIGH-1：ask_agent 空窗 fallback 拉的是最老 80 条不是最新。**
现象：query_records 默认 `ORDER BY ts_start ASC`，fallback `LIMIT 80` 拿到全库最早 80 条，跟用户问"最近"完全反向。
解决：query_records 加 `order='asc'|'desc'` 参数；ask_agent fallback 传 `order='desc'`；actual_hours 用 `min(rows ts)` 而非 `rows[-1]`。

**MED-2：BM25 排序是空操作。**
现象：`ORDER BY (SELECT bm25(records_fts) FROM records_fts WHERE record_id=r.id)` 子查询无 MATCH，bm25() 返回常量，"BM25 排序"事实未生效。
解决：CTE 重写 —— `WITH fts_hits AS (SELECT record_id, bm25(records_fts) AS rank_score FROM records_fts WHERE records_fts MATCH ?)` 外层 JOIN，bm25 与 MATCH 同 query level。

生产验证：搜"鸣潮游戏"前 5 全是 Wuthering Waves 高密度命中（4-5 次提及），证明 BM25 真生效。

### 问题2：第二轮 review LOW-1/LOW-2（commit 60887e4）

- **LOW-1**：BM25 测试 insert 顺序反了，"退化为 rowid"和"真 BM25"产生同结果，测试没捕获力。改：insert 顺序反转 + 加第三条更高密度行，期望输出 = insert 逆序。
- **LOW-2**：FTS 路径 `order='desc'` 被静默忽略（footgun）。改：`ORDER BY h.rank_score ASC, r.ts_start {direction}` 二级排序。

### 问题3：RRF 测试期望写错（commit f34651e）

`test_rrf_weights_bias_channel` 断言 `unweighted[0]=='k'` 错了 —— a 同时在两路 list 得分 2/61 > k 单路 1/60，a 才第一。**代码无误，是我测试期望算错**。重写成单 item per channel 干净用例。

---

## 知识清单

- **emb 失败绝不阻塞主流程**：vlm_done 是durable state，emb 是 best-effort，backfill 兜底。
- **backfill 双失败模式**：poison 行 skipped 续跑 vs 端点 down 连续失败 abort —— 区分"单点坏"和"全坏"。
- **FTS5 aux 函数（bm25/rank）只在 MATCH 同 query level 有效**，子查询无 MATCH 静默返常量。
- **测试要能区分"真生效"和"巧合"**：BM25 测试 insert 逆序才有捕获力。
- **vector_search 暂用 numpy 全表扫**，个人数据量 <10ms，不上 ANN。

---

## 待办 / 遗留

- [ ] **Phase 2c 接线（未做，故意）**：vector_search + RRF 接到 MCP search_activity + REST /v1/search。要改 app.py / build_mcp_server，与 infra agent 路由工作冲突，**等 infra 收工后做**，约 30 分钟纯增量。
- [ ] Phase 3：ask_agent 改语义检索（query embed → cosine top 80 而非时间窗）—— 锦上添花。
- [ ] commit 2670dac 标"未接线"= 休眠代码，部署安全。
