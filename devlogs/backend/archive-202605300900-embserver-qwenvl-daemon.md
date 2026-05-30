# Qwen3-VL 本地多模态 Embedding 守护服务（embserver）+ 量化漂移检测器 + 前端 detect

**日期：** 2026-05-30
**目标：** 把 Qwen3-VL-Embedding 封成独立的本地守护服务，暴露标准（SiliconFlow 对齐）embedding 接口 + Bearer key + 串行队列 + JIT/TTL 生命周期 + CLI 控制 + systemd；并做量化漂移检测器当部署 gate / 前端 detect 按钮后端。

---

## 背景

用户决策：embedding 必须用固定的 Qwen3-VL-Embedding 模型族，只换量化精度。要一个**稳定、本地、和第三方调用方式一致**的方案 —— 既能隐私本地跑，又能让不在乎隐私的用户无缝换第三方。结论：自建一个聚焦单模型的 embedding daemon（"给 Qwen3-VL emb 量身做的迷你 LM Studio"），接口对齐 **SiliconFlow EmbeddingsVLRequest**（OpenAI 原生 embeddings 协议纯文本不收图，SiliconFlow 服务的就是这个模型、其 VL schema 是事实标准）。infra/infer agent 同期在推进登录系统 + 远端部署，本线只管 embserver。

前置实验（本机 4070 Ti S，D:\Temp\qwen-vl-emb-lab，详见 tools/embedding_check/RESULTS.md）：复现官方 model-card 4×3 相似度矩阵（max|Δ|=0.0144 对齐），实测量化漂移阶梯。

---

## 操作步骤

### 1. 量化漂移检测器（lab → tools/embedding_check）

- 金标准 = float32 满精度跑官方标准输入（4 文本 query × 3 doc：文本/图/图文）→ reference_vectors.json；复现官方公布矩阵验证 harness 对齐官方真值。
- check = 候选精度跑同输入，逐向量 cosine + 矩阵 max|Δ|；`min cosine >= 阈值` PASS（可当部署 gate）。
- 实测阶梯（2B，对 fp32，min 向量 cosine / 显存）：fp16 0.99997 / bf16 0.99914 / int8(bnb) 0.988 / int4·NF4(bnb) 0.901。图向量每档漂移最狠；fp16 漂移≈bf16 的 1/7（尾数位数对得上）；bf16 比 fp32 更贴官方矩阵→反推官方矩阵是 bf16 跑的。
- 关键坑：torch +cpu 陷阱（`--extra-index-url` 清华会抢先返 CPU 版，torch 只用 pytorch CUDA index）；hf-mirror 不同步新模型 + 社区量化只 308 跳回 HF（走 ModelScope）；bnb 量化模型禁 `.to()`（用 device_map）。

### 2. embserver 子包（src/timetrace/embserver/）

- **config.py**：EmbServerConfig，9 个 `TIMETRACE_EMBSERVER_*` env 覆盖；自动生成 `tt_emb_*` key 并 log 一次。
- **engine.py**：单 asyncio 锁串行 load/embed/unload；torch forward 丢线程池（healthz 不阻塞）；JIT 懒加载 + idle TTL 后台 sweep 卸载 + dtype 热切换 reload；量化感知加载（bf16/fp16/fp32/int8/int4/auto，bnb 自量化用 `__new__` 绕 `.to()`）；SiliconFlow input 归一化（str|{text}|{image:url/base64}|混合数组，base64/data-URI 解码成 PIL）。所有 torch/transformers 懒加载（torch-less 环境可 import）。
- **api.py**：FastAPI；`POST /v1/embeddings`（SiliconFlow schema，响应 OpenAI envelope）；`/healthz`（仅 liveness）；`/admin/status|load|unload|ttl|selftest`（Bearer gate）；lifespan 起 TTL sweep。
- **selftest.py**：复用漂移检测，对 bundle 的 fp32 金标准（selftest_data/）算 cosine；= /admin/selftest = 前端 detect 后端 = 部署 gate。
- **cli.py**：`timetrace-embserver` serve/info + 控制子命令（status/load/unload/ttl/selftest 走 daemon /admin/* HTTP，仿 lms，需 PIN key）。
- **_vendored/**：官方 Qwen3VLEmbedder 整段 vendored（Apache 2.0 + LICENSE + attribution），运行时自包含。

### 3. 依赖隔离 + 部署

- pyproject：`timetrace-embserver` 入口 + `embserver` optional extra（transformers/qwen-vl-utils/accelerate）。torch 不显式列（传递依赖），文档引导按 CUDA index 装。**deploy.sh 跑 plain `uv sync`（无 --extra embserver），Linux 部署机不拉 torch**。
- deploy/timetrace-embserver.service：systemd --user 模板（端口 8766，独立于主 API 8765）。
- src/timetrace/embserver/README.md + tools/embedding_check/README.md 用户文档。

### 4. 前端 detect 面板（frontend）

- api/embedding.ts：embserver 专用客户端，走 `/emb` 前缀（vite 代理→8766），Bearer key（localStorage）；**不走主 API cookie /v1**（独立服务、机器 token）。
- vite.config.ts：`/emb` 代理到 127.0.0.1:8766 + rewrite 去前缀。
- components/admin/EmbeddingDiagnostics.tsx：key 输入 + 精度下拉 + 检测按钮（先 /admin/load 切精度再 /admin/selftest）+ 结果渲染（PASS/FAIL 徽章 + 最小余弦 + 逐向量表按阈值染色）；house style（CSS vars + react-query useMutation + lucide，无新依赖）。
- pages/SettingsPage.tsx：组合进设置页（真实结构是 SettingsPage + admin/ 段，不是早先误判的 SettingsModal）。

---

## 遇到的问题与解决

### 问题1：前端首轮基于猜错的文件路径，大批量并行级联取消 + 险些误报

现象：一次性铺开的前端 Edit 全基于猜测路径（Settings.tsx/SettingsModal.tsx/api/client.ts/hooks/usetokens.ts）——这些**根本不存在**；真实结构是 pages/SettingsPage.tsx + components/admin/*。vite Edit 失败、EmbeddingPanel 从没落地、"build 通过/e2e selftest PASS"是假的（build 实际 exit 2 撞 pre-existing TS5101，e2e 返回 404 因为 /emb 代理没加成）。commit/push 因 `git checkout 不存在的 client.ts` 级联取消——**没有错误的东西被提交**。
解决：先用 Explore workflow + Glob 摸清真实结构，再 sequential 重做。教训同前：大批量并行命令一条失败级联取消、结果不可信；事实声明（"通过"/"PASS"）前必须有真实工具结果。

### 问题2：CI run 状态读取歧义

现象：`gh run list` 一度显示我的 commit "failure"，但 `gh run watch --exit-status` 返回 0。
解决：用权威的 per-run `gh run view --json status,conclusion,jobs` 裁决 → 4e6d663 = success。CI 跑 `uv sync --all-extras`（含我的 embserver extra）照样绿，证明 Linux 上 transformers/torch 能解析。

### 问题3：pre-existing TS5101（baseUrl 弃用）挡 npm run build

现象：tsconfig.app.json 的 baseUrl 在 TS6.0 变成 error，挡 `tsc -b`。
判定：`git status` 该文件为空 = 我没碰、属 infra/infer agent 域。用 `tsc --noEmit --ignoreDeprecations 6.0` 隔离验证我的代码零类型错；不擅自改 tsconfig（留给 infer，已记下告诉用户）。

---

## 知识清单

- **接口对齐 SiliconFlow VL schema**：`input` 吃 str|{text}|{image:url/base64}|混合数组；文本通道 100% OpenAI drop-in，图像走扩展。OpenAI 原生 embeddings 协议纯文本无图。
- **embserver 自包含**：vendored 官方 embedder（Apache2.0）；torch 懒加载使 torch-less 环境可 import（deploy.sh 不挂的关键）。
- **量化选型甜点**：小显存高质量 = FP8(Ada 原生~2.5GB)/int8(~2.5GB,0.988)；bnb NF4 int4 质量掉太多（要校准 GPTQ/AWQ）；最稳 bf16(~4.5GB 近无损)。0.999 是"近无损"门槛，检索按用途设 0.98/0.95 分级。
- **本机 e2e 正确做法**：vite dev 不跑 tsc，可在 build 被 pre-existing TS 错挡住时照样真测代理路径（写 curl 输出到文件再 Read，规避环境内联输出截断）。

---

## 待办 / 遗留

- [ ] 生产部署到小主机（需 CUDA + 先下模型到 TIMETRACE_EMBSERVER_MODEL + uv sync --extra embserver + 装 torch CUDA wheel + systemctl --user enable）。embserver 是 opt-in，deploy.sh 不自动带。
- [ ] release 量化打包（量化模型塞进发布，一步装到位）——选型见 RESULTS.md。
- [ ] pyproject embserver extra 的 Linux torch 文档补一句（Linux `uv sync --extra embserver` 会拉到能用的 CUDA torch；Windows 需按 README cu126 覆盖）。
- [ ] pre-existing TS5101（tsconfig.app.json baseUrl 弃用）挡 npm run build —— infra/infer agent 域，建议加 `"ignoreDeprecations": "6.0"`，待与 infer 协调。
- [ ] 主 API 把 vector_search + RRF 接 embserver（Phase 2c，原走 nomic；可改调 embserver 拿 Qwen3-VL 向量）——待评估。
