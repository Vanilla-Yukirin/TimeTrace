# TimeTrace Embedding Server (`timetrace-embserver`)

A standalone local daemon that wraps **Qwen3-VL-Embedding** and exposes a
**SiliconFlow-compatible** `/v1/embeddings` endpoint (text + image), with Bearer
auth and strictly serial inference. Runs as its own process on its own port
(default **8766**) — one of several local interfaces TimeTrace can occupy.

Why a separate service: OpenAI's `/v1/embeddings` is text-only; multimodal
embedding needs an extended input schema. We align to SiliconFlow's
`EmbeddingsVLRequest` (it serves this exact model), so **text callers are drop-in
OpenAI-compatible** and **image callers** use the documented `{image: url|base64}`
extension — and privacy-indifferent users can point the same client at a
third-party endpoint instead.

## Install (only on a machine that will SERVE embeddings — needs a CUDA GPU)

Heavy deps (torch/transformers) are behind the optional `embserver` extra and
are **not** part of the base install (keeps the Linux server deploy light).

```bash
# 1. torch FIRST, from the correct PyTorch index. Do NOT add a mirror
#    --extra-index-url here — it can silently pull a +cpu build.
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
# 2. the extra (transformers / qwen-vl-utils / accelerate)
uv sync --extra embserver
# 3. (optional) bitsandbytes for int8/int4 self-quantization
uv pip install bitsandbytes
```

### Download the model

```bash
# ModelScope is fast in China and always has the latest Qwen (hf-mirror lags).
modelscope download --model Qwen/Qwen3-VL-Embedding-2B \
  --local_dir ~/TimeTraceData/models/Qwen3-VL-Embedding-2B
```

## Run

```bash
export TIMETRACE_EMBSERVER_MODEL=~/TimeTraceData/models/Qwen3-VL-Embedding-2B
export TIMETRACE_EMBSERVER_API_KEY=tt_emb_pin_me   # PIN it (see Auth)
timetrace-embserver            # serve on 127.0.0.1:8766
timetrace-embserver info       # print resolved config (no GPU needed)
```

## Control (CLI → running daemon over HTTP, like `lms`)

```bash
timetrace-embserver status                 # loaded? dtype? vram? idle?
timetrace-embserver load --dtype int8      # (re)load, swap precision/size
timetrace-embserver unload                 # free VRAM now
timetrace-embserver ttl 900                # idle auto-unload after 900s (0 = resident)
timetrace-embserver selftest               # drift vs bundled fp32 golden master
```

Control subcommands need `TIMETRACE_EMBSERVER_API_KEY` **pinned** (shared with
the daemon). An auto-generated key can't be known by a separate CLI process.

## API

```bash
# text (OpenAI drop-in)
curl http://127.0.0.1:8766/v1/embeddings \
  -H "Authorization: Bearer $TIMETRACE_EMBSERVER_API_KEY" \
  -d '{"input": "A woman playing with her dog on a beach at sunset."}'

# image (SiliconFlow extension: url or base64 data-URI)
curl http://127.0.0.1:8766/v1/embeddings -H "Authorization: Bearer $KEY" \
  -d '{"input": {"image": "https://example.com/x.jpg"}}'

# mixed list
curl ... -d '{"input": ["text a", {"text": "b"}, {"image": "data:image/jpeg;base64,..."}]}'
```

Response is the standard OpenAI envelope: `{object, data:[{embedding,...}], model, usage}`.
`/healthz` (no auth) = liveness only; it does NOT prove the model is loadable
(JIT load happens on the first `/v1/embeddings`).

## Precision / VRAM selection

`TIMETRACE_EMBSERVER_DTYPE` (or `load --dtype`): `bfloat16` (default) | `float16`
| `float32` | `int8` | `int4` | `auto` (a prequantized checkpoint honors its own
config). Measured drift vs fp32 golden master on the 2B model (see
`tools/embedding_check/RESULTS.md`):

| dtype | min vector cosine | VRAM (approx) | notes |
|---|---|---|---|
| float16 | 0.99997 | ~4.5GB | near-lossless |
| **bfloat16** | 0.99914 | ~4.5GB | **default — near-lossless** |
| int8 (bnb) | 0.988 | ~2.5GB | borderline; fine for retrieval |
| int4/NF4 (bnb) | 0.901 | ~1.5GB | too lossy; use calibrated GPTQ/AWQ int4 instead |

`selftest` (= `/admin/selftest`) reproduces this check live against the bundled
golden master — use it as a deployment gate after any precision change.

## Auth

Bearer token, shape `tt_emb_<urlsafe>` (matches the rest of TimeTrace). If unset,
one is generated and logged once at startup — pin it via env to keep it stable
and to enable the control subcommands.

## Concurrency

Strictly serial: a single lock guards load/embed/unload, so requests queue and
run one-at-a-time (no multi-model / multi-channel). The blocking torch forward
runs in a thread executor so `/healthz` stays responsive while an embed is in flight.
