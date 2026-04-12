"""Screenshot capture and thumbnail generation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import mss
import mss.tools
import structlog
from PIL import Image

if TYPE_CHECKING:
    from timetrace.config import StorageConfig

logger = structlog.get_logger(__name__)

_THUMB_SIZE = (320, 200)  # Max thumbnail dimensions (preserves aspect ratio)


def capture_active_window(
    record_id: str,
    storage_cfg: StorageConfig,
    hwnd: int | None = None,
) -> tuple[Path, Path, str, int, int] | None:
    """Capture a screenshot and generate a thumbnail.

    Returns (img_path, thumb_path, sha256_hash, width, height) relative to
    storage_cfg.data_dir, or None on failure.
    """
    try:
        with mss.mss() as sct:
            if hwnd is not None:
                region = _hwnd_to_region(hwnd, sct)
            else:
                region = sct.monitors[1]  # Primary monitor

            sct_img = sct.grab(region)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        width, height = img.size
        sha256 = _image_hash(img)

        img_path = _build_path(storage_cfg.screenshots_dir, record_id, "png")
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(img_path), format="PNG", optimize=False)

        thumb = img.copy()
        thumb.thumbnail(_THUMB_SIZE, Image.Resampling.LANCZOS)
        thumb_path = _build_path(storage_cfg.thumbs_dir, record_id, "jpg")
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb.save(str(thumb_path), format="JPEG", quality=75, optimize=True)

        # Return paths relative to data_dir for portability
        rel_img = img_path.relative_to(storage_cfg.data_dir)
        rel_thumb = thumb_path.relative_to(storage_cfg.data_dir)

        logger.debug(
            "screenshot.captured",
            record_id=record_id,
            width=width,
            height=height,
            sha256=sha256[:8],
        )
        return rel_img, rel_thumb, sha256, width, height

    except Exception:
        logger.warning("screenshot.failed", record_id=record_id, exc_info=True)
        return None


def _build_path(base: Path, record_id: str, ext: str) -> Path:
    """Return a date-bucketed file path: base/YYYY/MM/DD/<record_id>.<ext>"""
    from datetime import date

    today = date.today()
    return base / f"{today.year}" / f"{today.month:02d}" / f"{today.day:02d}" / f"{record_id}.{ext}"


def _image_hash(img: Image.Image) -> str:
    """Compute SHA-256 of raw RGB bytes."""
    h = hashlib.sha256()
    h.update(img.tobytes())
    return h.hexdigest()


def _hwnd_to_region(hwnd: int, sct: mss.base.MSSBase) -> dict:
    """Convert a window handle to an mss capture region dict.

    Falls back to the primary monitor if the window bounds are invalid.
    """
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        if right > left and bottom > top:
            return {"left": left, "top": top, "width": right - left, "height": bottom - top}
    except Exception:
        pass
    return sct.monitors[1]
