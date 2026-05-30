# embedding_check — Qwen3-VL-Embedding quantization drift detector

Standalone, reproducible tool that validates a Qwen3-VL-Embedding checkpoint /
precision against a full-precision (fp32) **golden master** and against the
official model-card similarity matrix.

This is the research/dev sibling of the live `timetrace-embserver selftest`
(which bundles a pre-generated golden master). Use this one to **regenerate** the
golden master or to test arbitrary checkpoints/precisions from scratch.

See [RESULTS.md](RESULTS.md) for measured numbers (the full fp16/bf16/int8/int4
drift ladder) and the gotchas (torch +cpu trap, hf-mirror lag, bnb `.to()`).

## How it works

- **Golden master**: fp32 run of the official standard inputs (4 text queries ×
  3 documents: text / image / text+image) → `reference_vectors.json`. Reproduces
  the official model-card 4×3 similarity matrix to validate the harness.
- **Drift check**: a candidate precision runs the same inputs; per-vector cosine
  vs the golden master + similarity-matrix max|Δ|. `min cosine >= threshold` → PASS.

## Setup

```bash
uv venv --python 3.12 .venv
# torch ONLY from the PyTorch CUDA index (a mirror --extra-index pulls +cpu)
uv pip install --python .venv/Scripts/python.exe torch torchvision \
  --index-url https://download.pytorch.org/whl/cu126
uv pip install --python .venv/Scripts/python.exe \
  "transformers>=4.57.3" "qwen-vl-utils>=0.0.14" "accelerate>=1.0" modelscope bitsandbytes
# official embedder code (Qwen3VLEmbedder) — cloned next to these scripts
git clone --depth 1 https://github.com/QwenLM/Qwen3-VL-Embedding.git
# model (ModelScope is fast in China)
.venv/Scripts/modelscope.exe download --model Qwen/Qwen3-VL-Embedding-2B \
  --local_dir models/Qwen3-VL-Embedding-2B
# the standard image fixture
mkdir -p fixtures && curl -L -o fixtures/demo.jpeg \
  https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen-VL/assets/demo.jpeg
```

`_lab.py` expects, next to itself: `Qwen3-VL-Embedding/` (cloned repo),
`models/Qwen3-VL-Embedding-2B/`, `fixtures/demo.jpeg`.

## Run

```bash
python capture_reference.py --dtype float32   # golden master -> reference_vectors.json
python check.py --dtype float32               # self-check (must be cosine 1.0)
python check.py --dtype bf16                   # quantization drift
python check.py --dtype fp16
python check.py --dtype int8                   # bitsandbytes self-quant
python check.py --dtype int4
python check.py --dtype auto --model models/<prequantized-checkpoint>  # FP8/GPTQ/AWQ
```

PASS/FAIL exit code (0/1) makes it usable as a deployment gate.
