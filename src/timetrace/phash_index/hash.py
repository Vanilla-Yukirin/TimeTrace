"""Perceptual hash (pHash) computation and blob (de)serialisation."""

from __future__ import annotations

import numpy as np
from PIL import Image

_N = 32  # Downsample size before DCT
_LOW = 8  # Keep top-left 8x8 low-frequency block


def _build_dct_matrix(n: int) -> np.ndarray:
    """Orthonormal DCT-II basis matrix (n x n). dct(x) = M @ x."""
    k = np.arange(n, dtype=np.float64).reshape(-1, 1)
    i = np.arange(n, dtype=np.float64).reshape(1, -1)
    m = np.cos(np.pi * (i + 0.5) * k / n)
    m[0] *= 1.0 / np.sqrt(2.0)
    m *= np.sqrt(2.0 / n)
    return m


_DCT_MATRIX = _build_dct_matrix(_N)


def compute_phash(img: Image.Image) -> int:
    """Return a 64-bit perceptual hash for the given image.

    Classic DCT pHash: grayscale → 32x32 → DCT-II → top-left 8x8 → median threshold.
    """
    gray = img.convert("L").resize((_N, _N), Image.Resampling.LANCZOS)
    arr = np.asarray(gray, dtype=np.float64)
    dct = _DCT_MATRIX @ arr @ _DCT_MATRIX.T
    low = dct[:_LOW, :_LOW]
    median = float(np.median(low))
    bits = (low > median).flatten().astype(np.uint8)
    return int.from_bytes(np.packbits(bits).tobytes(), "big")


def phash_to_blob(h: int) -> bytes:
    return h.to_bytes(8, "big")


def phash_from_blob(b: bytes) -> int:
    assert len(b) == 8, f"phash blob must be 8 bytes, got {len(b)}"
    return int.from_bytes(b, "big")


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()
