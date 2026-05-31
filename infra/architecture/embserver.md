# embserver（多模态 embedding 守护服务）

> 一个**独立进程**的多模态 embedding 守护服务：把 Qwen3-VL-Embedding 模型包成 daemon，跑在端口 8766，对外提供兼容 SiliconFlow EmbeddingsVL / OpenAI 形状的 `/v1/embeddings`，吃文本 + 图。代码在 `src/timetrace/embserver/`，入口 `timetrace-embserver`。
>
> 注意区分：本文讲的 embserver 是 **Qwen3-VL 多模态**（文本 + 图像）embedding，与主 server 进程内那个纯文本 embedding 客户端（`server/embedding/client.py`）**完全是两回事**，关系见末节。

## 为何独立成一个进程

embserver 刻意不挂进主 server，也不进 `common/config.py` 的 `AppConfig`，而是自带一份 `embserver/config.py::EmbServerConfig`：

- **依赖隔离**：embserver 要 `torch` / `torchvision` / `transformers` / `qwen-vl-utils` / `accelerate`，是几个 GB 的重依赖，且 torch 在 Linux 部署机上要走 CUDA index。主 server（capture / API / DB / worker / VLM HTTP 客户端）必须保持轻量、`uv sync` 即装即跑。`engine.py` 把 `torch` / `transformers` / `qwen_vl_utils` 全部**惰性 import 在方法体内**（`# noqa: PLC0415`），就是为了让"只 import 这个模块"（如 `timetrace-embserver info`）不触发重依赖。
- **生命周期独立**：模型按需加载（JIT）、空闲后卸载（idle TTL），显存占用是脉冲式的。这套生命周期跟主 server 长驻的事件循环无关，单独一个进程便于 systemd 单独托管、单独重启、单独卸模型省显存。
- **依赖打包**：`transformers` / `qwen-vl-utils` / `accelerate` 只在 `pyproject.toml` 的 `[project.optional-dependencies]` 里的 `embserver` extra；torch/torchvision **故意不列**（必须先从正确的 PyTorch index 装，否则会被悄悄换成 `+cpu` 轮子）。部署机 `deploy.sh` 跑 plain `uv sync`，**不**拉这个 extra，所以 Linux server 永远不会去解析这些重依赖。

## 端口与入口

- 端口默认 **8766**（主 server 是 8765），host 默认 `127.0.0.1`（本地优先，不直接公网）。
- 第四个 `[project.scripts]`：`timetrace-embserver` → `embserver/cli.py:main`。
- 默认模型路径 `~/TimeTraceData/models/Qwen3-VL-Embedding-2B`（本地目录，不是 HF hub id），dtype 默认 `bfloat16`，idle TTL 默认 **0（常驻不卸）**，preload 默认关（JIT）。dim = 2048。

## HTTP API（SiliconFlow / OpenAI 兼容）

`embserver/api.py::create_app` 起一个 FastAPI app。公开面对齐 SiliconFlow 的 EmbeddingsVL 形状（这正是该模型事实上的多模态 embedding 接口），所以纯文本调用方是 drop-in OpenAI 兼容，图像调用方用 `{image: url|base64}` 扩展。

路由清单：

| 路由 | 鉴权 | 作用 |
|------|------|------|
| `GET /healthz` | 否 | liveness，返 `{"status":"ok","loaded": bool}`。**不证明模型可加载**（JIT 在首个 `/v1/embeddings` 才发生），语义对齐主 API 的 healthz |
| `GET /admin/status` | 是 | `engine.status()`：loaded / model / dtype / loaded_at / last_used / idle_seconds / idle_ttl_seconds / vram_mb |
| `POST /admin/load` | 是 | `engine.reload(dtype?, model_path?)`：换精度 / 换模型，先 unload 再懒加载 |
| `POST /admin/unload` | 是 | 立刻卸模型释放显存 |
| `POST /admin/ttl` | 是 | 在运行中 daemon 上改 `idle_ttl_seconds`（0 = 常驻） |
| `POST /admin/selftest` | 是 | 跑漂移 selftest（前端 detect 按钮的后端） |
| `POST /v1/embeddings` | 是 | 核心：算 embedding |

### `/v1/embeddings` 请求体

```jsonc
{
  "input": ...,                 // 见下文多形态
  "model": "...",               // 可选；仅回显在响应里，不切模型
  "encoding_format": "float"    // 当前固定 float（base64 未实装）
}
```

注意：**请求体没有 `dtype` 字段**。切 dtype 不走单次请求，而是走 `/admin/load`（见生命周期一节）——这跟"按请求热切精度"是两种设计，embserver 选了后者控制面集中在 `/admin/`。

### `input` 的多形态（`engine.py::normalize_input` / `_normalize_item`）

归一化逻辑把以下几种都收敛成 `list[{"text"?: str, "image"?: PIL|url}]`：

- `"some text"` → 一条文本
- `["a", "b"]` → 多条文本
- `{"text": ...}` / `{"image": ...}` / `{"text": ..., "image": ...}` → 一条结构化项
- `[{...}, {...}]` → 多条结构化项（文本字符串与图像 dict 可混排）
- 空 list / 既无 text 又无 image 的 dict → 400

图像的 `image` 字段由 `engine.py::_decode_image` 解析：`http(s)://` URL 原样透传（由 embedder 自己拉远程图）；`data:` data-URL 取逗号后段；裸 base64 直接 decode；解出字节后 `PIL.Image.open(...).convert("RGB")`。非法 base64 抛 `EngineError` → 400。

### 响应 envelope（OpenAI 形状）

返回标准 OpenAI embeddings envelope：`{"object":"list", "data":[{"object":"embedding","index":i,"embedding":[...]}, ...], "model": req.model or cfg.model_path.name, "usage": {"prompt_tokens":0,"total_tokens":0}}`。`usage` 当前是占位（恒 0）。

### 鉴权（Bearer，与主项目 token 同形）

`api.py::_require_key` 给所有 `/admin/*` 和 `/v1/embeddings` 套 Bearer 依赖：

- 期望值是 `cfg.api_key`。**首启若未配 key，`cli.py::_serve` 调 `cfg.ensure_api_key()` 自动生成 `tt_emb_<urlsafe32>` 并 `logger.warning` 打印一次**（提示设 `TIMETRACE_EMBSERVER_API_KEY` pin 住，否则每次重启重新生成）。
- 校验用 `secrets.compare_digest(token, expected)` 常量时间比较，`expected` 为空或不符一律 401。
- 命名习惯用 `tt_emb_` 前缀以便和主 server 的 `tt_live_`（业务 bearer）区分；但**前缀只是约定**，代码做的是字符串相等比较，不强制校验前缀。
- `/healthz` 不鉴权。

> 与主 server ingest 鉴权的差异：主 server 没 token 也强制 gate；embserver 是"没配 key 就自动生成一个并打日志"，本地默认仍然有 key。

## 串行队列（单 asyncio 锁）

`embserver/engine.py::EmbeddingEngine` 用**一把 `asyncio.Lock`** 串行化所有重活：load / embed / unload / reload 都在锁内完成。同一时刻只有一次推理在跑，并发请求自然排队。产品决策就是"严格串行"——单卡显存有限、Qwen3-VL forward 吃显存，串行比让多请求抢显存更稳。

实际 torch 计算（`_load_blocking` / `_embed_blocking`）用 `loop.run_in_executor(None, ...)` 丢线程池，避免阻塞事件循环，让 healthz 在一次 embed 跑着时仍能响应。

## 生命周期：JIT load + idle TTL unload + dtype 热切

- **JIT load**：模型不在进程启动时加载，而是 `ensure_loaded()` 在首个请求（或 `/admin/load`）时懒加载（双检锁）。加载丢线程池跑，记 `embserver.load.start` / `.done` 日志带耗时。`preload=True`（env `TIMETRACE_EMBSERVER_PRELOAD=1`）则在 `lifespan` 启动时就 `ensure_loaded()`，失败只 warning 不致命。
- **idle TTL unload**：`api.py::_ttl_sweep` 后台每 **15 秒**（`_TTL_SWEEP_SECONDS`）调一次 `engine.maybe_unload_if_idle()`，空闲（以 `last_used`/`loaded_at` 为锚）超过 `idle_ttl_seconds` 就 `unload()`（删 embedder 引用 + `torch.cuda.empty_cache()`，记 `embserver.ttl.unload`）。`idle_ttl_seconds <= 0` 禁用自动卸载（默认即 0，常驻）。
- **dtype / 模型热切换**：`engine.reload(dtype?, model_path?)`（经 `/admin/load`）先 `unload()` 再改 `self.dtype` / `self.model_path` 再 `ensure_loaded()`。所以前端 / CLI 能在不重启进程的前提下切精度或换模型尺寸，selftest 跨 dtype 测漂移也靠它。
- 生命周期挂 FastAPI `lifespan`：启动起 `_ttl_sweep` 任务（+ 可选 preload），退出 cancel sweeper + `unload()`。
- `engine.status()` 暴露 `loaded / model / dtype / loaded_at / last_used / idle_seconds / idle_ttl_seconds / vram_mb`，经 `/admin/status` 可查。`vram_mb` 通过 `torch.cuda.memory_allocated()` 读，无 CUDA 返 None。

## CLI（控制面走 `/admin/` HTTP，仿 lms）

`embserver/cli.py` 一个手写子命令分发器。关键设计：除 `serve` 和 `info` 外，所有控制类子命令都是**通过 httpx 打到正在运行的 daemon 的 `/admin/` 端点**，CLI 进程本身**不 import torch、不动模型**——对标 `lms`（LM Studio CLI）控制本地模型守护进程，保持 CLI 零重依赖。

| 子命令 | 做什么 | 实现 |
|--------|--------|------|
| `serve`（默认，无参数时） | 前台起 daemon（uvicorn + JIT/TTL 生命周期） | 进程内 `create_app(cfg, EmbeddingEngine(...))` |
| `info` | 打印解析后的配置 + listen addr + api_key | 纯本地，读 `EmbServerConfig`，会 `ensure_api_key()` |
| `status` / `ps` | 查运行中 daemon 加载状态 | GET `/admin/status` |
| `load [--dtype D] [--model PATH]` | (重新)加载，可换精度 / 换模型 | POST `/admin/load` |
| `unload` | 立刻卸模型释放显存 | POST `/admin/unload` |
| `ttl <seconds>` | 改 idle TTL（0 = 常驻） | POST `/admin/ttl` |
| `selftest [--threshold T]` | 漂移检测（vs 内置 golden master） | POST `/admin/selftest` |

**控制类子命令必须 pin `TIMETRACE_EMBSERVER_API_KEY`**：自动生成的 key 只活在 daemon 进程里，独立的 CLF 进程无从得知，所以 `_client_call` 在 `cfg.api_key` 为空时直接报错退出（exit 2）。打不到 daemon（没起）→ 报 "cannot reach daemon" 并 exit 1。

## selftest = 量化漂移检测 = 部署 gate = 前端 detect 后端

`embserver/selftest.py::run_selftest` 一份代码身兼三职，与独立工具 `tools/embedding_check` 同一套判据，但驱动的是**活的 engine**：

1. **对齐内置金标准**：拿官方 model-card 的标准输入（4 条 query 文本 + 3 条 doc：文本 / 图 / 图文，`selftest_data/demo.jpeg`），在当前已加载的 dtype 下算向量，逐向量对 `selftest_data/reference_vectors.json` 里的 fp32 golden master 算余弦相似度。
2. **量化漂移量化**：`min_cosine`（最差那条向量 vs 金标准）+ 相似度矩阵 `max|Δ| vs ref` + `max|Δ| vs official`（官方公布矩阵）。
3. **部署 gate / 判据**：`min_cosine >= threshold`（默认 0.999）→ `verdict: PASS`，否则 FAIL。CLI `selftest` 可当部署后探针。

前端 `detect` 面板（`frontend/src/components/admin/EmbeddingDiagnostics.tsx`）经 vite 代理调它：`vite.config.ts` 把 `/emb/*` 反代到 `127.0.0.1:8766` 并 strip `/emb` 前缀（`/emb/admin/selftest` → `8766/admin/selftest`）。前端要带 `tt_emb_*` machine bearer key（存 localStorage，**不是** session cookie）。

## 量化阶梯实测

`tools/embedding_check/RESULTS.md`（本机 RTX 4070 Ti SUPER 16GB / torch 2.12.0+cu126）对 `Qwen/Qwen3-VL-Embedding-2B`（dim=2048），以 **float32 为金标准**实测：

| 精度 | min cosine vs fp32 | 矩阵 max\|Δ\| vs fp32 | 显存(约) | 0.999 gate |
|------|--------------------|-----------------------|----------|------------|
| float32 (self-check) | 1.000000 | 0.000000 | ~8.5 GB | PASS |
| fp16 | 0.999969 | 0.000662 | ~4.5 GB | PASS |
| bf16 | 0.999135 | 0.004374 | ~4.5 GB | PASS |
| int8 (bnb LLM.int8) | 0.987986 | 0.023810 | ~2.5 GB | FAIL |
| int4 / NF4 (bnb) | 0.901032 | 0.131480 | ~1.5 GB | FAIL |

结论（RESULTS.md 原文支撑）：

- 漂移随精度单调增长，检测器能清楚区分每一档。**图向量（D1/D2）每档都是漂移最狠的**——视觉塔对量化最敏感。
- 反推：官方公布的相似度矩阵实为 **bf16** 跑出（bf16 比 fp32 更贴官方矩阵，0.0125 < 0.0144）。
- 选型：最稳 **bf16（~4.5GB，近无损）**，是默认；小显存甜点 **FP8 或 int8（~2.5GB，0.988）**；**bnb NF4 int4（~1.5GB）质量掉太多不直接用**，要 1.5GB 得上带校准的 int4（GPTQ/AWQ，未实测）。
- `0.999` 是"近无损"门槛，int8/int4 FAIL 不等于不可用，检索场景可按用途分级（0.98 / 0.95）。

## dtype 实现细节（`engine.py::_load_blocking`）

dtype 字符串映射：`float32/fp32`、`bfloat16/bf16`、`float16/fp16` 直接给 `Qwen3VLEmbedder(torch_dtype=...)`；`auto` 让预量化 checkpoint 走自己的 config；`int8` / `int4` / `nf4` 走 `_load_quant_blocking`——用 `transformers.BitsAndBytesConfig`（int8 = `load_in_8bit`；int4/nf4 = `load_in_4bit` + nf4 + double-quant + bf16 compute dtype），并用 `device_map={"":0}` 绕开 `Qwen3VLEmbedder.__init__` 末尾那个对 bnb 模型非法的 `.to(device)`。所以 int8/int4 需要 `bitsandbytes`（CUDA only，本机 Windows CUDA wheel 直接可用、即时量化）。

embed forward 由 vendored `Qwen3VLEmbedder.process(items)` 完成，返回 `[N, dim]` 已 L2-normalize 的张量，`_embed_blocking` 再 `.float().cpu().tolist()`。

## 安装与运行

```bash
# 1. 先从正确的 PyTorch index 装 torch/torchvision（别加别的 extra-index，否则换成 +cpu）
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
# 2. 装 embserver extra（transformers / qwen-vl-utils / accelerate）
uv sync --extra embserver
# 3.（可选，int8/int4 自量化）pip install bitsandbytes
# 4. 下模型到默认路径（hf-mirror 没同步该模型，走 ModelScope）
modelscope download --model Qwen/Qwen3-VL-Embedding-2B \
  --local_dir ~/TimeTraceData/models/Qwen3-VL-Embedding-2B

# 前台起服务
uv run --extra embserver timetrace-embserver        # serve 是默认
# 另一个终端控制（需 pin TIMETRACE_EMBSERVER_API_KEY）
uv run timetrace-embserver status
uv run timetrace-embserver load --dtype int8
uv run timetrace-embserver unload
uv run timetrace-embserver ttl 1800
uv run timetrace-embserver selftest --threshold 0.999
```

### 配置（`TIMETRACE_EMBSERVER_*`，共 7 个）

`config.py::EmbServerConfig.from_env()` 读：`HOST`（默认 127.0.0.1）、`PORT`（8766）、`MODEL`（默认 `~/TimeTraceData/models/Qwen3-VL-Embedding-2B`）、`DTYPE`（`bfloat16|float16|float32|int8|int4|auto`）、`API_KEY`（缺省自动生成 + log 一次；pin 它才能用控制子命令）、`TTL`（idle 秒数，0 = 常驻）、`PRELOAD`（`1` = 启动即加载，否则 JIT）。统一前缀 `TIMETRACE_EMBSERVER_`。

### systemd 托管

`deploy/timetrace-embserver.service`（`systemctl --user`）：`ExecStart=%h/.local/bin/timetrace-embserver`，环境里写好 MODEL / DTYPE=bfloat16 / TTL=900，建议 pin `API_KEY`（或用 EnvironmentFile），`Restart=on-failure`，`ReadWritePaths=%h/TimeTraceData`。service 注释明确：前置步骤（torch 装、`uv sync --extra embserver`、下模型、`loginctl enable-linger`）**deploy.sh 不管**——embserver 是 opt-in，故意排除在 plain `uv sync` 部署路径之外；且需要机器有 CUDA GPU。靠 JIT + TTL，service 长驻也不会一直占满显存。

embserver **不经 nginx 暴露公网**（绑 127.0.0.1:8766），与项目"本地优先 / Web UI 永不公网"一致；远程访问走 SSH 隧道。

## 与主 server EmbeddingClient 的关系

| | 主 server 内 `EmbeddingClient`（`server/embedding/client.py`） | embserver |
|---|---|---|
| 进程 | 主 server 进程内 | 独立进程（8766） |
| 模型 | 纯文本 embedding（768 维，packed float32 BLOB） | Qwen3-VL 多模态（文本 + 图，dim 2048） |
| 接入 | worker `_embed_and_save` / `_backfill_embeddings` 回写 `analysis_results.text_embedding`，喂 `db.vector_search` | 当前**未**接入主 worker / 检索路径 |
| 依赖 | httpx 调外部 embedding 服务，主依赖即可 | 自带 torch / transformers，opt-in extra |

embserver 目前是**独立的多模态 embedding 能力储备**：能以图搜图、能给图文混合内容算向量，但还没接进主 server 的 worker / 检索。两条 embedding 通道当前各管各的；未来 embserver 可能作为统一向量源替代或补充纯文本通道。

## vendored embedder（Apache-2.0）

`_vendored/qwen3_vl_embedding.py::Qwen3VLEmbedder` 改编自 Qwen 官方 [QwenLM/Qwen3-VL-Embedding](https://github.com/QwenLM/Qwen3-VL-Embedding)（Apache-2.0）的 embedding 推理示例，**只 vendored embedding forward 路径，不含训练代码**。自包含进仓库是为了不依赖某个可能漂移的外部 pip 包，把推理路径钉死在自己手里。`engine.py` 的 bnb 量化分支也直接复用它的 `Qwen3VLForEmbedding` / `MAX_LENGTH` / `MIN_PIXELS` 等模块级常量来手搭量化版 embedder。
