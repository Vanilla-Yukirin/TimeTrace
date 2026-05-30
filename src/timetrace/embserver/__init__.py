"""Local Qwen3-VL multimodal embedding daemon (optional ``embserver`` extra).

A standalone FastAPI service that wraps the (vendored) Qwen3-VL-Embedding model
and exposes a SiliconFlow-compatible ``/v1/embeddings`` endpoint with Bearer
auth and strictly serial inference. Runs as its own process on its own port
(default 8766) — entry point ``timetrace-embserver``.

torch/transformers/qwen-vl-utils are NOT base dependencies; install with
``pip install timetrace[embserver]`` (or ``uv sync --extra embserver``).
"""
