"""Check the official release on demand without downloading executables."""

import re

import httpx

from timetrace import __version__

_REPO = "Vanilla-Yukirin/TimeTrace"
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)\Z")


async def check_for_update() -> dict:
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(
            f"https://api.github.com/repos/{_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        release = response.json()
    tag = release.get("tag_name", "")
    latest = _VERSION.fullmatch(tag)
    current = _VERSION.fullmatch(__version__)
    if latest is None or current is None or release.get("draft") or release.get("prerelease"):
        raise ValueError("未识别的正式版版本号")
    available = tuple(map(int, latest.groups())) > tuple(map(int, current.groups()))
    return {
        "current": __version__,
        "latest": tag.removeprefix("v"),
        "available": available,
        "url": f"https://github.com/{_REPO}/releases/tag/{tag}",
    }
