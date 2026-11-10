"""Encode captured pixels once for the standalone client's durable Outbox."""

import hashlib
import io
from dataclasses import dataclass

from PIL import Image

from timetrace.client.capture.settings import ScreenshotSettings
from timetrace.common.phash_hash import compute_phash

_FORMATS = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}


@dataclass(frozen=True)
class EncodedFrame:
    image: bytes
    thumb: bytes
    extension: str
    width: int
    height: int
    phash: int | None
    pixel_hash: str


def _encode(image: Image.Image, extension: str, quality: int) -> bytes:
    output = io.BytesIO()
    options = {"optimize": True} if extension != "webp" else {"method": 4}
    if extension != "png":
        options["quality"] = quality
    image.save(output, _FORMATS[extension], **options)
    return output.getvalue()


def encode_frame(
    image: Image.Image, settings: ScreenshotSettings, *, limit: int, previous_hash: str | None
) -> EncodedFrame | None:
    image = image.convert("RGB")
    digest = hashlib.sha256(f"{image.width}x{image.height}:".encode())
    digest.update(image.tobytes())
    pixel_hash = digest.hexdigest()
    if pixel_hash == previous_hash:
        return None

    full = image.copy()
    quality = settings.quality
    extension = settings.image_format
    if settings.smart_compression:
        full.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
    encoded = _encode(full, extension, quality)
    if settings.smart_compression and limit:
        while len(encoded) > limit:
            if extension != "png" and quality > 55:
                quality = max(55, quality - 15)
            elif max(full.size) > 640:
                side = max(640, int(max(full.size) * 0.72))
                full.thumbnail((side, side), Image.Resampling.LANCZOS)
            else:
                break
            encoded = _encode(full, extension, quality)
    if limit and len(encoded) > limit:
        raise ValueError("截图超过当前上传大小限制")

    image.thumbnail((640, 400), Image.Resampling.LANCZOS)
    return EncodedFrame(
        encoded,
        _encode(image, "jpg", 85),
        extension,
        full.width,
        full.height,
        compute_phash(image),
        pixel_hash,
    )
