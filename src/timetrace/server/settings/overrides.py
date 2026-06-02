"""Per-app classification overrides + knowledge — the backing store for the
Settings KV editor.

ONE settings row (``key='app_overrides'``) is the single source of truth read by
three consumers, so the wire shape can never drift between them:

- the **worker** turns it into a :class:`RuleSet` so a user-pinned category
  (e.g. ``Vanish → social``) DETERMINISTICALLY overrides the VLM's per-frame
  jitter (``decide_category`` gives a rule weight 2.0 > the VLM's 1.5);
- the **VLM describe** call injects the free-text ``note`` for an app so the
  model has user-provided context for genuinely ambiguous windows;
- the **HTTP route** (``routes/settings.py``) reads/writes it for the editor.

Stored shape (JSON)::

    {"version": 1, "apps": {
        "vanish": {"category": "social", "note": "公司内部 IM，工作沟通用"},
        "claude": {"category": "work",   "note": "AI 编程助手"}
    }}

Keys are the app-name token the user types; they are matched as a case-
insensitive SUBSTRING against ``records.app_name`` — same semantics as
``RuleSet.match``. ``category`` may be null (note-only, no forced category);
``note`` may be empty (category-only override).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from timetrace.server.rules.engine import RuleSet

if TYPE_CHECKING:
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

SETTINGS_KEY = "app_overrides"

# Mirrors db/sqlite.py _BUILTIN_CATEGORIES — the only valid override categories.
_BUILTIN_CATEGORY_IDS = frozenset(
    {"work", "study", "social", "entertainment", "system", "uncategorized"}
)
_MAX_APPS = 200
_MAX_NOTE_CHARS = 300


def _empty() -> dict:
    return {"version": 1, "apps": {}}


async def load_overrides(db: Database) -> dict:
    """Return the stored overrides dict, or an empty default. Never raises:
    corrupt / missing JSON degrades to the empty default (worker keeps working)."""
    raw = await db.get_setting(SETTINGS_KEY)
    if not raw:
        return _empty()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("overrides.corrupt_json_ignored")
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("apps"), dict):
        return _empty()
    return data


def validate_overrides(data: dict) -> dict:
    """Return a cleaned copy of ``data``; raise ``ValueError`` on a bad shape.

    Drops blank keys, caps note length, rejects unknown categories and oversized
    maps so a typo can't poison ``category_final`` grouping downstream.
    """
    if not isinstance(data, dict):
        raise ValueError("overrides must be an object")
    apps_in = data.get("apps", {})
    if not isinstance(apps_in, dict):
        raise ValueError("'apps' must be an object")
    if len(apps_in) > _MAX_APPS:
        raise ValueError(f"too many apps (max {_MAX_APPS})")
    apps_out: dict[str, dict] = {}
    for key, val in apps_in.items():
        k = (key or "").strip()
        if not k:
            continue
        if not isinstance(val, dict):
            raise ValueError(f"override for '{key}' must be an object")
        cat = val.get("category")
        if cat is not None and cat != "" and cat not in _BUILTIN_CATEGORY_IDS:
            raise ValueError(f"unknown category '{cat}' for app '{k}'")
        note = (val.get("note") or "")[:_MAX_NOTE_CHARS]
        apps_out[k] = {"category": cat or None, "note": note}
    return {"version": 1, "apps": apps_out}


async def save_overrides(db: Database, data: dict) -> dict:
    """Validate + persist; return the cleaned dict that was stored."""
    clean = validate_overrides(data)
    await db.set_setting(SETTINGS_KEY, json.dumps(clean, ensure_ascii=False))
    logger.info("overrides.saved", apps=len(clean["apps"]))
    return clean


def build_ruleset(overrides: dict) -> RuleSet:
    """Adapt the overrides map into the ``list[dict]`` :class:`RuleSet` expects.

    Only entries that pin a category become rules (note-only entries don't),
    so an empty / category-less map yields the same empty-RuleSet behavior as
    before this feature existed.
    """
    rules = [
        {"app": k, "category": v["category"]}
        for k, v in overrides.get("apps", {}).items()
        if v.get("category")
    ]
    return RuleSet(rules=rules)


def find_note(overrides: dict, app_name: str | None) -> str | None:
    """Free-text note for ``app_name``, matched as a case-insensitive substring
    (first match wins, mirroring ``RuleSet.match`` order). Keys are user tokens
    (e.g. 'vanish'); ``app_name`` is the full name — so we scan, not dict-index.
    """
    app = (app_name or "").lower()
    if not app:
        return None
    for k, v in overrides.get("apps", {}).items():
        note = v.get("note")
        if note and k.lower() in app:
            return note
    return None
