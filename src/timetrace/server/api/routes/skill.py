"""Serve the TimeTrace Claude Code skill for download.

The vision: a user's local Claude Code (or any agent) fetches this one URL,
saves the returned markdown into ``.claude/skills/timetrace/SKILL.md``, and
thereby learns how to drive TimeTrace over MCP (the 6 tools + the label-only
rule). So this route just returns the canonical ``skills/timetrace/SKILL.md``
as ``text/markdown``.

Open (no auth): it's documentation, not access — the MCP surface it describes is
still bearer-gated. Mounted outside ``business_deps`` so a fresh agent with no
cookie/token can still download the instructions that tell it how to get a token.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["skill"])

# routes/skill.py → routes → api → server → timetrace → src → <repo root>
_SKILL_PATH = Path(__file__).resolve().parents[5] / "skills" / "timetrace" / "SKILL.md"


@router.get("/skill", response_class=PlainTextResponse)
async def get_skill() -> PlainTextResponse:
    """Return the TimeTrace MCP skill (SKILL.md) as text/markdown."""
    try:
        text = _SKILL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="skill file not found on server") from None
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
