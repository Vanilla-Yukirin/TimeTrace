# 语义向量检索接进搜索路由：/v1/search/text（关键词 FTS5 + 语义向量 RRF）

**日期：** 2026-06-05
**目标：** 把早已实装却零调用方的 `db.vector_search`（文本 embedding 余弦）接进一个真实的搜索端点，做出"关键词 + 语义"双通道融合检索；与并行进行的 audit 功能**互不冲突**地完成。

---

## 背景

这是 PLAN.md 里的**头号未做项**："语义/文本向量检索接进搜索路由——`db.vector_search` 已实装但全库零调用方，RRF 融合仍只跑 pHash+FTS5/LIKE 两路，向量是沉睡的第三路"。

**并发约束（关键）**：本次开工时，**另一个 agent 正在同一个工作区上并行做 audit 日志页**，touch 了后端共享文件 `app.py`、`sqlite.py`（加 `query_audit_records`）、新建 `routes/audit.py`，外加一堆前端。用户明确要求"你们一起改不冲突"。所以本任务的核心约束是：**在不碰对方未提交文件的前提下**把向量检索做完。

就绪度（开工前只读核对）：
- `db.vector_search(query_vec: bytes, limit, start_ms, end_ms) -> list[(record_id, score)]` 已实装（纯 numpy 余弦，返回 **record_id**，不是 screenshot_id）。
- `EmbeddingClient.embed(text) -> bytes` + `cosine_similarity` 已有；`retrieval.py::reciprocal_rank_fusion` 已写好但**零调用方**。
- box 上 **5511 条** `text_embedding` 已入库（nomic-embed-text-v1.5 / 768d / 3072B），embedding 模型已在 `.env` 配置（`TIMETRACE_EMBEDDING_MODEL`）。

---

## 操作步骤（本次实际做的）

### 1. 冲突面排查（只读）

`git status` 发现工作区有 audit agent 的未提交改动：`M app.py`、`M sqlite.py`、`?? routes/audit.py`（+ 后来的前端）。逐文件比对后定下**零冲突路线**：

| 文件 | audit agent | 我（向量检索） | 冲突 |
|---|---|---|---|
| `routes/search.py` | 不碰 | **加 `/v1/search/text` 端点**（router 已在 app.py 挂载，加路由不用动 app.py） | ✅ 无 |
| `db/sqlite.py` | 加 `query_audit_records` | **不碰**（`vector_search` 已存在直接调用；enrichment 走 search.py 内联 SQL） | ✅ 无 |
| `retrieval.py` | 不碰 | 用现成 `reciprocal_rank_fusion` | ✅ 无 |
| `tests/test_search_text.py` | 不碰 | 新建 | ✅ 无 |
| **`app.py` / `bootstrap.py`** | 改 app.py（挂 audit 路由） | **要暴露 `embedding_client` 到 app.state** | ⚠️ **唯一交集**→ 推迟（见下） |

关键判断：**`vector_search` 已实装，我不需要改 sqlite.py**，所以他的 `sqlite.py +69` 完全不挡我。唯一交集是 app.py 那几行——按用户定的 **B 方案推迟**到 audit agent 提交后再补。

### 2. 实现端点（`routes/search.py`，append 到末尾）

`GET /v1/search/text`（`/search/by-image` 的免图版，纯文本搜索）：

- **关键词通道**：复用 `db.query_records(keyword=q)` 的 FTS5/LIKE 自动分流（≥3 字 trigram BM25，<3 字多字段 LIKE）+ BM25 排序，提取 record_id 列表。
- **语义通道**：`getattr(app.state, "embedding_client", None)` → `embed(q)` → `db.vector_search(qvec)` → record_id 列表。
- **融合**：`retrieval.reciprocal_rank_fusion([keyword_ids, semantic_ids])`（k=60），record 级。
- **enrichment**：新增 search.py 内联 helper `_fetch_records_by_ids`（一条 `WHERE r.id IN (...)` 批量查 app_name/window_title/vlm_desc/category/首图 thumb），**不新增 sqlite.py 方法**——刻意绕开 audit agent 在动的 sqlite.py。
- **优雅降级**：`embedding_client` 缺失或 `embed` 抛 `EmbeddingError` → `semantic_channel="unavailable"`，退化为关键词-only，搜索照常工作。

返回形状：`{items:[{record_id, ts_start, app_name, window_title, vlm_desc, category_final, thumb_path, match:{rrf_score, keyword_rank, semantic_rank}}], total, keyword_channel, semantic_channel}`。

### 3. 测试（`tests/test_search_text.py`，5 例）

**关键技巧**：测试里直接 `app.state.embedding_client = _FakeEmbedder(...)` 注入——app.state 是可变命名空间，端点读它，**与 `create_app` 签名无关**，所以**不依赖 app.py 改动也能端到端测**。

- 融合：rid_a 仅关键词命中 / rid_b 仅语义命中（embedding==查询向量）/ rid_c 两者皆无 → 断言 a、b 都出、c 不出、a 因双通道 RRF 最高排第一。
- 无 embedder → `semantic_channel="unavailable"`、只回关键词命中。
- embed 抛错 → 同样降级。
- 分类过滤、空 q→400。

### 4. 验证 + 提交

```
uv run ruff check / format  → 干净
uv run pytest               → 全量 479 passed
git add src/timetrace/server/api/routes/search.py tests/test_search_text.py  # 只暂存我的 2 文件
git commit                  → 4a74c26（318 insertions）
```

`git status` 确认暂存区只有我的 2 文件，audit agent 的 app.py/sqlite.py/前端**一概没进来**。`4a74c26` 已推送到 origin/feature/refactor-split（推送当时 clash 拦过几次，后成功）。

---

## ⭐ 还差的一步：生产接线（推迟方案 · 详细）

**端点已上线安全，但语义通道在生产环境还没真正激活** —— 因为 `app.state.embedding_client` 还没挂上（`create_app` 不接收 embedding_client，`build_server_components` 也没传给它，目前只传给了 worker）。在接上之前，线上 `/v1/search/text` 永远走 `semantic_channel="unavailable"`（关键词-only）。

### 为什么推迟

`app.py` 是和 audit agent 的**唯一交集文件**。开工时他的 app.py 改动**未提交**，此时我若改 app.py，`git add app.py` 会把他未提交的 audit 改动一起暂存进我的 commit（共享工作区通病）。故按用户的 B 方案：**先做完所有非 app.py 的部分，等他提交 app.py 后再补这几行**。

### 阻塞已解除（2026-06-05 01:48）

**audit agent 已提交 `84a96c4`（feat(audit): Phase A 审计日志页），叠在我 `4a74c26` 之上。** 也就是说他的 app.py 改动**已落地为提交**，现在我再改 app.py，`git add app.py` 只会暂存**我的新行**（他的已在历史里）。**接线可以安全进行了。**

### 确切要改的（3-4 行）

**① `src/timetrace/server/api/app.py`**
- 顶部加 import（给类型注解用）：`from timetrace.server.embedding.client import EmbeddingClient`（参照 VLMClient 的 import 写法；如有 TYPE_CHECKING 块可放进去）。
- `create_app(...)` 签名加参数（放在 `auth_cfg` 等之后）：
  ```python
  embedding_client: EmbeddingClient | None = None,
  ```
- app.state 赋值块里（挨着 `app.state.vlm_client = vlm_client` 附近）加：
  ```python
  app.state.embedding_client = embedding_client
  ```

**② `src/timetrace/server/bootstrap.py`**
- `build_server_components` 里已有 `embedding_client` 变量（`config.embedding` 存在时 `EmbeddingClient(config.embedding)`，否则 None）。在 `app = create_app(db, storage_cfg=..., ..., auth_cfg=config.auth)` 这个调用里追加一行：
  ```python
  embedding_client=embedding_client,
  ```

就这些。**不需要再改 search.py / 测试**（端点早就 `getattr(app.state,"embedding_client",None)` 读它了）。

### 接线后的完整推进流程

```
# 1. 改上面两个文件
# 2. 干净暂存（确认只暂存我的行）
git add src/timetrace/server/api/app.py src/timetrace/server/bootstrap.py
git diff --cached            # 核对：只有 embedding_client 接线，无 audit 残留
# 3. 验证
uv run ruff check src/ && uv run pytest -q     # 应仍全绿
# 4. 提交 + 推 feature（不触发部署）
git commit -m "feat(search): 暴露 embedding_client 到 app.state，激活 /v1/search/text 语义通道"
git push origin feature/refactor-split
# 5. 部署（push-to-deploy：推 deploy 分支即自动部署 box）
git push origin feature/refactor-split:deploy
#    → GH Actions deploy.yml(event=push,branch=deploy) → box git reset --hard origin/deploy + 重启
```

### 接线后的终极验证（"确保没问题" = 真实数据语义召回）

box 上有 **5511 条真实向量** + embedding 模型已配，部署后应能真正语义检索。用 **LAN `GTi13-Ultra`(192.168.2.105)**（在家）或 `GTi13-Ultra-2v4G`（在外）SSH 过去，在 box 本机打：

```bash
# A. 确认 embedding 客户端激活（日志应有 embedding.ready）
journalctl --user -u timetrace-server.service --since '5 min ago' | grep -i embedding

# B. 端点确认语义通道在线（带 bearer token；token 在「API Tokens」面板，勿写进 devlog）
curl -s -G 'http://127.0.0.1:8765/v1/search/text' \
  --data-urlencode 'q=摸鱼' -H 'Authorization: Bearer <box token>' | head -c 400
#    → 期望 "semantic_channel":"ok"
```

**语义相关性实测（核心）**：搜一个**字面不出现在描述里、但语义相关**的词，看语义通道有没有额外召回价值：
- `q=摸鱼` → 应召回 entertainment 类记录（即便描述里没"摸鱼"二字）；
- `q=写代码` → 应召回 VSCode/编程记录；
- 对比同一查询关键词-only（临时把 embedding 关掉或看 `match.semantic_rank` 为 null 的项）确认语义通道确实补充了关键词漏掉的结果。

前端接入由那位 agent 负责（他在动前端查询），我**不碰前端**。

---

## 本会话其他工作（从略，均已提交）

按用户要求只列一行，细节看 commit message / git 历史：

- `7562833` docs(infra)：清理过期架构 wiki 25 篇 + 重建 PLAN 未做项总表（2 个核对/编辑 workflow，去 KNN / MCP 4→6 工具 / 死链修正）。
- `a8c4f36` fix(worker)：修截图迟到致记录被无图跳过、永不分类的采集时序竞态（ingest 截图到达后 `requeue_skipped_for_vlm`）。
- `51d8746` ci(deploy)：加 push-to-deploy（推 `deploy` 分支即部署），保留 workflow_dispatch；建 `deploy` 分支，实测自动部署成功。
- 三项**只调查未动手**：① 搜索慢的真因（实测 box 本机 16ms / 公网 ~2s——是 nginx+frp 隧道延迟地板，非 DB；在家应走 `ssh -L 8765` 直连）；② 回填存量 4764 条"有图未分类"（A 无描述 4230 / B 有描述 534；3355 无图信号不动；客户端**不用停**）；③ GPU 用 Prometheus 看是"低均值高尖峰"（均值 9.3%）。

---

## 知识清单

- **零冲突并行开发**：和别人共享工作区时，把新功能塞进**新文件 / 对方不碰的现有文件**；要改的共享文件（这里是 app.py）**最后做、等对方提交后再 `git add`**，避免 `git add <file>` 把对方未提交改动打包进自己 commit。
- **`app.state` 注入测试法**：FastAPI 的 `app.state` 是可变命名空间，测试里 `create_app()` 后直接 `app.state.x = fake` 即可，**绕开 `create_app` 签名**——让端点测试不依赖尚未完成的依赖注入接线。
- **加路由不一定要改 app.py**：往**已 `include_router` 的现有 router**（search.router）加 `@router.get(...)` 就自动挂载，无需碰 app.py。
- **`vector_search` 返回 record_id 不是 screenshot_id**：文本搜索在 record 级融合（不同于 `/search/by-image` 的 screenshot 级）。
- **nomic embedding 无 task 前缀**：worker 存 vlm_desc 时没加 `search_document:` 前缀，故查询也保持无前缀（对称）。未来想提质要加 nomic 前缀，但需重嵌全部 5511 条——属另一独立改动。
- **优雅降级**：检索端点对"语义不可用"必须降级到关键词，别让 embedding 端点挂了就整个搜索 500。

---

## 待办 / 遗留

- [ ] **（阻塞已解除，可立即做）生产接线**：app.py + bootstrap 那 3-4 行（详见上方「还差的一步」），然后 push feature → push deploy 部署 → box 真实数据语义召回验证。
- [ ] 前端把搜索框接到 `/v1/search/text`（那位 agent 域，我不碰）。
- [ ] （可选·独立改动）给 nomic 加 `search_query:`/`search_document:` task 前缀提质——需重嵌全部向量。
- [ ] 本会话另有未推进的待办：存量 4764 条回填（A 方案，需授权动 box DB）、在家走 `ssh -L` 加速搜索访问——均"先不推进，写完文档再议"。
