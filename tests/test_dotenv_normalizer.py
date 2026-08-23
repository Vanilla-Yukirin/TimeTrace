from __future__ import annotations

from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from timetrace.common.dotenv_normalizer import normalize_dotenv


def test_normalize_dotenv_preserves_values_without_interpolation(tmp_path: Path) -> None:
    legacy_env = tmp_path / ".env"
    legacy_env.write_text(
        "\n".join(
            (
                'TIMETRACE_VLM_API_KEY="quoted-secret"',
                "TIMETRACE_LITERAL_DOLLAR=abc$TOKEN",
                'TIMETRACE_BRACED_DOLLAR="abc${TOKEN}"',
                r"TIMETRACE_BACKSLASH='C:\models\vlm'",
                r"TIMETRACE_SINGLE_QUOTE='it\'s-local'",
                'TIMETRACE_HASH="value # fragment"',
                "TIMETRACE_EMPTY=",
                "",
            )
        ),
        encoding="utf-8",
    )

    normalized = normalize_dotenv(legacy_env)

    assert "TIMETRACE_LITERAL_DOLLAR='abc$TOKEN'\n" in normalized
    assert "TIMETRACE_BRACED_DOLLAR='abc${TOKEN}'\n" in normalized
    assert dotenv_values(stream=StringIO(normalized), interpolate=False) == {
        "TIMETRACE_VLM_API_KEY": "quoted-secret",
        "TIMETRACE_LITERAL_DOLLAR": "abc$TOKEN",
        "TIMETRACE_BRACED_DOLLAR": "abc${TOKEN}",
        "TIMETRACE_BACKSLASH": r"C:\models\vlm",
        "TIMETRACE_SINGLE_QUOTE": "it's-local",
        "TIMETRACE_HASH": "value # fragment",
        "TIMETRACE_EMPTY": "",
    }
