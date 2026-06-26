"""Tests for ``timetrace-server tokens`` admin commands.

Each test redirects ``DEFAULT_TOKEN_DIR`` via monkeypatch to a tmp_path so
real ~/.config is never touched. Only auth.py owns the module-level
attribute now (admin_cmd reads it via ServerAuth helpers).
"""

from __future__ import annotations

import json

from timetrace.server import admin_cmd, auth


class _Capture:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def joined(self) -> str:
        return "\n".join(self.lines)


def _redirect_token_dir(monkeypatch, tmp_path):
    """Point auth's DEFAULT_TOKEN_DIR at tmp_path/tokens.json."""
    monkeypatch.setattr(auth, "DEFAULT_TOKEN_DIR", tmp_path)


# --------------------------------------------------------------------- #
# tokens list                                                            #
# --------------------------------------------------------------------- #


def test_tokens_list_empty(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    out = _Capture()
    rc = admin_cmd.run(["tokens", "list"], out=out)
    assert rc == 0
    assert "(no tokens)" in out.joined


def test_tokens_list_shows_label_and_suffix_only(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    # Pre-seed a token via the auth path
    auth.ServerAuth.load_or_generate(tmp_path)
    out = _Capture()
    rc = admin_cmd.run(["tokens", "list"], out=out)
    assert rc == 0
    assert "default" in out.joined  # label shown
    # Full token value must NOT appear
    full = json.loads((tmp_path / "tokens.json").read_text())["tokens"][0]["value"]
    assert full not in out.joined


# --------------------------------------------------------------------- #
# tokens add                                                             #
# --------------------------------------------------------------------- #


def test_tokens_add_mints_and_appends(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    auth.ServerAuth.load_or_generate(tmp_path)  # seed default

    out = _Capture()
    rc = admin_cmd.run(["tokens", "add", "Yuki-Laptop"], out=out)
    assert rc == 0

    data = json.loads((tmp_path / "tokens.json").read_text())
    assert len(data["tokens"]) == 2
    new_label = [t["label"] for t in data["tokens"] if t["label"] != "default"][0]
    assert new_label == "Yuki-Laptop"

    # Full new value appears in output (so user can copy it)
    new_value = [t["value"] for t in data["tokens"] if t["label"] == "Yuki-Laptop"][0]
    assert new_value in out.joined
    assert new_value.startswith("tt_live_")


def test_tokens_add_refuses_duplicate_label(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    auth.ServerAuth.load_or_generate(tmp_path)

    out = _Capture()
    rc = admin_cmd.run(["tokens", "add", "default"], out=out)
    assert rc == 1
    assert "already exists" in out.joined


# --------------------------------------------------------------------- #
# tokens revoke                                                          #
# --------------------------------------------------------------------- #


def test_tokens_revoke_by_label(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    auth.ServerAuth.load_or_generate(tmp_path)
    admin_cmd.run(["tokens", "add", "Yuki-Laptop"], out=_Capture())

    out = _Capture()
    rc = admin_cmd.run(["tokens", "revoke", "default"], out=out)
    assert rc == 0
    data = json.loads((tmp_path / "tokens.json").read_text())
    labels = [t["label"] for t in data["tokens"]]
    assert "default" not in labels
    assert "Yuki-Laptop" in labels


def test_tokens_revoke_by_suffix(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    # Pin a deterministic token. A *randomly generated* value's last-8 suffix can
    # start with '-' (the base64url alphabet includes it), and argparse then treats
    # `tokens revoke -<…>` as an option rather than the positional identifier —
    # which flaked ~1/64 of CI runs. Use a known, dash-free suffix so this test
    # exercises the revoke-by-suffix matching, not argparse's dash handling.
    auth.ServerAuth.write_tokens(
        [auth.TokenEntry(value="tt_live_deadbeefcafe12345678", label="default", created_at=1)]
    )

    out = _Capture()
    rc = admin_cmd.run(["tokens", "revoke", "12345678"], out=out)
    assert rc == 0
    data = json.loads((tmp_path / "tokens.json").read_text())
    assert data["tokens"] == []


def test_tokens_revoke_no_match(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    auth.ServerAuth.load_or_generate(tmp_path)

    out = _Capture()
    rc = admin_cmd.run(["tokens", "revoke", "nope"], out=out)
    assert rc == 1
    assert "no token matches" in out.joined


def test_tokens_revoke_empty_file(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    out = _Capture()
    rc = admin_cmd.run(["tokens", "revoke", "any"], out=out)
    assert rc == 1


# --------------------------------------------------------------------- #
# info                                                                   #
# --------------------------------------------------------------------- #


def test_info_prints_paths(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    out = _Capture()
    rc = admin_cmd.run(["info"], out=out)
    assert rc == 0
    assert "data_dir" in out.joined
    assert "db_path" in out.joined
    assert "token_file" in out.joined
    assert "listen_addr" in out.joined


# --------------------------------------------------------------------- #
# perms hardening                                                        #
# --------------------------------------------------------------------- #


def test_token_file_chmod_600_on_posix(tmp_path, monkeypatch):
    _redirect_token_dir(monkeypatch, tmp_path)
    auth.ServerAuth.load_or_generate(tmp_path)
    token_file = tmp_path / "tokens.json"

    if hasattr(token_file, "stat"):
        st = token_file.stat()
        # On POSIX expect 0o600 (and only 0o600). On Windows the test is a no-op:
        # NTFS doesn't model POSIX permission bits the same way.
        import sys

        if sys.platform != "win32":
            assert (st.st_mode & 0o777) == 0o600
