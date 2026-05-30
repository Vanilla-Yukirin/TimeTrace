"""EmbeddingEngine — owns the Qwen3-VL model, serializes all inference.

Concurrency model (per product decision): strictly serial. A single
``asyncio.Lock`` guards load / embed / unload so requests queue and run
one-at-a-time. The blocking torch forward runs in the default executor so the
event loop stays responsive to health checks while one embed is in flight.

torch/transformers/qwen-vl-utils are imported lazily inside methods so that
merely importing this module (e.g. for `timetrace-embserver info`) does not
require the heavy `embserver` extra to be installed.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# bitsandbytes on-the-fly self-quantization modes vs plain dtypes.
_QUANT_MODES = {"int8", "int4", "nf4"}


class EngineError(RuntimeError):
    """Model load / inference failure surfaced to the API layer."""


class EmbeddingEngine:
    def __init__(
        self, model_path: Path, dtype: str = "bfloat16", idle_ttl_seconds: int = 0
    ) -> None:
        self.model_path = Path(model_path)
        self.dtype = dtype
        self.idle_ttl_seconds = idle_ttl_seconds
        self._embedder: Any | None = None
        self._lock = asyncio.Lock()
        self._loaded_at: float | None = None
        self._last_used: float | None = None

    # ---- lifecycle ------------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._embedder is not None

    def _load_blocking(self) -> Any:
        import torch  # noqa: PLC0415

        from ._vendored.qwen3_vl_embedding import Qwen3VLEmbedder  # noqa: PLC0415

        path = str(self.model_path)
        if self.dtype in _QUANT_MODES:
            return self._load_quant_blocking(path, self.dtype)
        if self.dtype == "auto":  # prequantized checkpoint honors its own config
            return Qwen3VLEmbedder(model_name_or_path=path, torch_dtype="auto")
        dtypes = {
            "float32": torch.float32,
            "fp32": torch.float32,
            "bfloat16": torch.bfloat16,
            "bf16": torch.bfloat16,
            "float16": torch.float16,
            "fp16": torch.float16,
        }
        return Qwen3VLEmbedder(model_name_or_path=path, torch_dtype=dtypes[self.dtype])

    @staticmethod
    def _load_quant_blocking(path: str, mode: str) -> Any:
        """bitsandbytes self-quant. Bypasses Qwen3VLEmbedder.__init__'s trailing
        .to(device) (illegal on bnb models) by building with device_map instead."""
        import torch  # noqa: PLC0415
        from transformers import BitsAndBytesConfig  # noqa: PLC0415
        from transformers.models.qwen3_vl.processing_qwen3_vl import (  # noqa: PLC0415
            Qwen3VLProcessor,
        )

        from ._vendored import qwen3_vl_embedding as m  # noqa: PLC0415

        if mode == "int8":
            qcfg = BitsAndBytesConfig(load_in_8bit=True)
        else:  # int4 / nf4
            qcfg = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            )

        emb = m.Qwen3VLEmbedder.__new__(m.Qwen3VLEmbedder)
        emb.max_length = m.MAX_LENGTH
        emb.min_pixels = m.MIN_PIXELS
        emb.max_pixels = m.MAX_PIXELS
        emb.total_pixels = m.MAX_TOTAL_PIXELS
        emb.fps = m.FPS
        emb.max_frames = m.MAX_FRAMES
        emb.default_instruction = "Represent the user's input."
        emb.model = m.Qwen3VLForEmbedding.from_pretrained(
            path, trust_remote_code=True, quantization_config=qcfg, device_map={"": 0}
        )
        emb.processor = Qwen3VLProcessor.from_pretrained(path, padding_side="right")
        emb.model.eval()
        return emb

    # ---- public async API ----------------------------------------------

    async def ensure_loaded(self) -> None:
        if self._embedder is not None:
            return
        async with self._lock:
            if self._embedder is not None:
                return
            logger.info("embserver.load.start", model=str(self.model_path), dtype=self.dtype)
            t0 = time.monotonic()
            try:
                self._embedder = await asyncio.get_running_loop().run_in_executor(
                    None, self._load_blocking
                )
            except Exception as exc:  # noqa: BLE001
                raise EngineError(f"model load failed: {exc}") from exc
            self._loaded_at = time.time()
            logger.info("embserver.load.done", seconds=round(time.monotonic() - t0, 1))

    async def embed(self, items: list[dict]) -> list[list[float]]:
        """Serialized embedding. items are already normalized to the embedder's
        {"text":?, "image": PIL|url} shape."""
        await self.ensure_loaded()
        async with self._lock:
            try:
                vecs = await asyncio.get_running_loop().run_in_executor(
                    None, self._embed_blocking, items
                )
            except Exception as exc:  # noqa: BLE001
                raise EngineError(f"inference failed: {exc}") from exc
            self._last_used = time.time()
            return vecs

    def _embed_blocking(self, items: list[dict]) -> list[list[float]]:
        emb = self._embedder.process(items)  # [N, dim], L2-normalized
        return emb.float().cpu().tolist()

    async def unload(self) -> None:
        async with self._lock:
            if self._embedder is None:
                return
            self._embedder = None
            self._loaded_at = None
            try:
                import torch  # noqa: PLC0415

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            logger.info("embserver.unload")

    async def reload(self, dtype: str | None = None, model_path: str | None = None) -> None:
        """Swap dtype/model: unload current, point at new, lazy-load on next use.
        Used by /admin/load so the frontend can pick a precision/size."""
        await self.unload()
        if dtype:
            self.dtype = dtype
        if model_path:
            self.model_path = Path(model_path)
        await self.ensure_loaded()

    def idle_seconds(self) -> float | None:
        if not self.loaded:
            return None
        anchor = self._last_used or self._loaded_at or 0.0
        return max(0.0, time.time() - anchor)

    async def maybe_unload_if_idle(self) -> bool:
        """TTL sweep hook. Unload if resident and idle beyond ttl. Returns True if unloaded."""
        if self.idle_ttl_seconds <= 0 or not self.loaded:
            return False
        idle = self.idle_seconds()
        if idle is not None and idle >= self.idle_ttl_seconds:
            logger.info("embserver.ttl.unload", idle_seconds=round(idle, 1))
            await self.unload()
            return True
        return False

    def _vram_mb(self) -> float | None:
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                return round(torch.cuda.memory_allocated() / 1024 / 1024, 1)
        except Exception:  # noqa: BLE001
            pass
        return None

    def status(self) -> dict:
        return {
            "loaded": self.loaded,
            "model": self.model_path.name,
            "dtype": self.dtype,
            "loaded_at": self._loaded_at,
            "last_used": self._last_used,
            "idle_seconds": self.idle_seconds(),
            "idle_ttl_seconds": self.idle_ttl_seconds,
            "vram_mb": self._vram_mb() if self.loaded else None,
        }


# ---- input normalization (SiliconFlow VL schema -> embedder dicts) -------


def _decode_image(value: str) -> Any:
    """url string passes through; data-URI / raw base64 -> PIL.Image."""
    from PIL import Image  # noqa: PLC0415

    if value.startswith(("http://", "https://")):
        return value  # embedder handles remote urls
    b64 = value.split(",", 1)[1] if value.startswith("data:") else value
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EngineError(f"invalid base64 image: {exc}") from exc
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _normalize_item(item: str | dict) -> dict:
    if isinstance(item, str):
        return {"text": item}
    if isinstance(item, dict):
        out: dict = {}
        if item.get("text") is not None:
            out["text"] = item["text"]
        if item.get("image") is not None:
            out["image"] = _decode_image(item["image"])
        if not out:
            raise EngineError("input object must have 'text' and/or 'image'")
        return out
    raise EngineError(f"unsupported input item type: {type(item).__name__}")


def normalize_input(value: Any) -> list[dict]:
    """SiliconFlow EmbeddingsVLRequest input -> list of embedder dicts.
    Accepts str | {text|image} | list of those."""
    if isinstance(value, (str, dict)):
        return [_normalize_item(value)]
    if isinstance(value, list):
        if not value:
            raise EngineError("input list is empty")
        return [_normalize_item(it) for it in value]
    raise EngineError(f"unsupported input type: {type(value).__name__}")
