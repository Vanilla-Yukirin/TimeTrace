"""Normalize a legacy dotenv file for safe Docker Compose consumption."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values


def _single_quote(value: str) -> str:
    """Encode a parsed value without allowing Compose interpolation."""
    escaped = value.replace("'", "\\'")
    return f"'{escaped}'"


def normalize_dotenv(path: Path) -> str:
    """Parse dotenv syntax without interpolation and emit canonical dotenv."""
    values = dotenv_values(path, interpolate=False, encoding="utf-8")
    lines = [f"{key}={_single_quote(value)}" for key, value in values.items() if value is not None]
    return "".join(f"{line}\n" for line in lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize dotenv values without expanding literal dollars."
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    sys.stdout.write(normalize_dotenv(args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
