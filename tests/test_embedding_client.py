"""EmbeddingClient unit tests — mock the underlying openai.embeddings.create.

Real-endpoint smoke test lives in test_embedding_smoke.py (not yet written —
should follow test_vlm_smoke.py pattern: read .env, skip if no key).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from timetrace.common.config import EmbeddingConfig
from timetrace.server.embedding.client import (
    EmbeddingClient,
    EmbeddingError,
    cosine_similarity,
)


def _fake_resp(vec: list[float]):
    return SimpleNamespace(data=[SimpleNamespace(embedding=vec)])


def _make_client(mock_create: AsyncMock, dim: int = 4) -> EmbeddingClient:
    cfg = EmbeddingConfig(base_url="https://x", api_key="sk-test", model="m", dim=dim)
    client = EmbeddingClient(cfg)
    client._client.embeddings.create = mock_create  # type: ignore[attr-defined]
    return client


async def test_embed_returns_packed_float32_bytes():
    mock = AsyncMock(return_value=_fake_resp([0.1, 0.2, -0.3, 0.4]))
    client = _make_client(mock)
    out = await client.embed("hello")

    assert isinstance(out, bytes)
    assert len(out) == 4 * 4  # 4 floats × 4 bytes
    decoded = np.frombuffer(out, dtype=np.float32)
    np.testing.assert_allclose(decoded, [0.1, 0.2, -0.3, 0.4], rtol=1e-6)


async def test_embed_raises_on_empty_input():
    client = _make_client(AsyncMock())
    with pytest.raises(EmbeddingError, match="empty"):
        await client.embed("")


async def test_embed_raises_on_dim_mismatch():
    """Provider returns wrong dim → caller must know (so DB doesn't store
    garbage that breaks search-side reshape)."""
    mock = AsyncMock(return_value=_fake_resp([0.1, 0.2]))  # 2 dims
    client = _make_client(mock, dim=4)  # expected 4
    with pytest.raises(EmbeddingError, match="dim mismatch"):
        await client.embed("hello")


async def test_embed_wraps_api_errors_in_embedding_error():
    mock = AsyncMock(side_effect=RuntimeError("connection refused"))
    client = _make_client(mock)
    with pytest.raises(EmbeddingError, match="connection refused"):
        await client.embed("hello")


async def test_embed_passes_correct_call_shape():
    mock = AsyncMock(return_value=_fake_resp([0.0, 0.0, 0.0, 0.0]))
    client = _make_client(mock)
    await client.embed("test input")

    call = mock.call_args
    assert call.kwargs["model"] == "m"
    assert call.kwargs["input"] == "test input"


def test_cosine_similarity_round_trip():
    a = np.asarray([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
    b = np.asarray([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
    c = np.asarray([0.0, 1.0, 0.0], dtype=np.float32).tobytes()
    d = np.asarray([-1.0, 0.0, 0.0], dtype=np.float32).tobytes()

    assert cosine_similarity(a, b) == pytest.approx(1.0)
    assert cosine_similarity(a, c) == pytest.approx(0.0)
    assert cosine_similarity(a, d) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero():
    """Defensive: dividing by zero norm would NaN. Caller should never see NaN."""
    a = np.zeros(4, dtype=np.float32).tobytes()
    b = np.asarray([1, 2, 3, 4], dtype=np.float32).tobytes()
    assert cosine_similarity(a, b) == 0.0


def test_cosine_similarity_known_pair():
    a = np.asarray([1.0, 1.0], dtype=np.float32).tobytes()
    b = np.asarray([1.0, 0.0], dtype=np.float32).tobytes()
    # cos(45°) = 1/sqrt(2)
    assert cosine_similarity(a, b) == pytest.approx(1 / np.sqrt(2), abs=1e-6)
