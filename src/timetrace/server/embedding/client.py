"""Text embedding client over the OpenAI /v1/embeddings protocol.

Works against OpenAI proper, LM Studio (nomic-embed / qwen3-vl-embedding),
vLLM, Ollama, or anything else honoring the OpenAI embeddings shape.

Returns vectors as packed float32 bytes (4 × dim bytes) ready for direct
SQLite BLOB storage. Search side reads them back with
``np.frombuffer(blob, dtype=np.float32)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import structlog
from openai import AsyncOpenAI

if TYPE_CHECKING:
    from timetrace.common.config import EmbeddingConfig

logger = structlog.get_logger(__name__)

_TIMEOUT_S = 30.0


class EmbeddingError(RuntimeError):
    """Wraps any failure in the embedding pipeline so worker can handle uniformly."""


class EmbeddingClient:
    """Async wrapper around OpenAI embeddings endpoint.

    Stateless except for the underlying httpx connection pool; safe to share
    one instance across the worker's N consumer coroutines.
    """

    def __init__(self, cfg: EmbeddingConfig) -> None:
        self._cfg = cfg
        # AsyncOpenAI manages an httpx.AsyncClient internally with connection
        # pooling — we close it on shutdown via aclose() to silence the
        # "open client" atexit warning that vlm.client also had.
        self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    @property
    def model(self) -> str:
        return self._cfg.model

    @property
    def dim(self) -> int:
        return self._cfg.dim

    async def aclose(self) -> None:
        await self._client.close()

    async def embed(self, text: str) -> bytes:
        """Embed ``text`` and return its vector as packed float32 bytes.

        Raises ``EmbeddingError`` on any failure (network / API / dim mismatch).
        Worker is expected to catch and log+skip — embedding failures must
        never block the vlm_done state transition.
        """
        if not text:
            raise EmbeddingError("cannot embed empty text")
        try:
            resp = await self._client.embeddings.create(
                model=self._cfg.model,
                input=text,
                timeout=_TIMEOUT_S,
            )
        except Exception as exc:
            raise EmbeddingError(f"embedding API call failed: {exc}") from exc

        try:
            vec = resp.data[0].embedding
        except (IndexError, AttributeError) as exc:
            raise EmbeddingError(f"embedding response shape unexpected: {exc}") from exc

        if len(vec) != self._cfg.dim:
            raise EmbeddingError(
                f"embedding dim mismatch: got {len(vec)}, expected {self._cfg.dim} "
                f"(model={self._cfg.model})"
            )

        # Pack to float32 bytes for compact storage. np.asarray copies if needed
        # to ensure C-contiguous + correct dtype. 768-dim → 3072 bytes.
        return np.asarray(vec, dtype=np.float32).tobytes()


def cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two float32-packed vectors.

    Free function (not on the client) so retrieval-side code in the search
    module can use it without holding a client reference. Returns float in
    [-1, 1]; higher = more similar.
    """
    va = np.frombuffer(a, dtype=np.float32)
    vb = np.frombuffer(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
