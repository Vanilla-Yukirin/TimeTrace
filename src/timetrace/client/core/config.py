"""ClientConfig — schema + load / save for `client.toml`.

`client.toml` is the persisted configuration the standalone
`timetrace-client` reads at startup. The single-process `uv run timetrace`
entry doesn't need it because in that mode capture is wired via
`InProcessBackend` directly. Once the two-process mode lands at P3a-5b,
the client process reads this file to learn server URL + auth token +
device identity + privacy mode.

Default location: `%USERPROFILE%/TimeTraceData/client.toml`.

Read uses stdlib `tomllib` (read-only). Write hand-formats the small fixed
schema rather than pulling in `tomli-w` for one file. The format kept
intentionally flat — three or four `[section]` blocks with primitive keys —
so the manual writer is one short function and a human can hand-edit it
without surprises.
"""

from __future__ import annotations

import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_PATH = Path.home() / "TimeTraceData" / "client.toml"
_DEFAULT_OUTBOX_DIR = Path.home() / "TimeTraceData" / "outbox"


@dataclass
class ServerSection:
    url: str = "http://127.0.0.1:8765"
    auth_token: str = ""


@dataclass
class DeviceSection:
    id: str = ""  # UUID, generated on first save if empty
    name: str = ""
    description: str = ""


@dataclass
class OutboxSection:
    root_dir: Path = field(default_factory=lambda: _DEFAULT_OUTBOX_DIR)


@dataclass
class UploadSection:
    # 0 means unlimited; positive ints are KB/s caps that P3a-5 will turn
    # into a token bucket inside OutboxSender.
    max_kbps: int = 0


@dataclass
class PrivacySection:
    # off  — no client-side filtering (current default)
    # text_only — block only on title keywords (P4 partial)
    # full — full OCR + classifier + blur pipeline (P4 main)
    mode: str = "off"


@dataclass
class ClientConfig:
    server: ServerSection = field(default_factory=ServerSection)
    device: DeviceSection = field(default_factory=DeviceSection)
    outbox: OutboxSection = field(default_factory=OutboxSection)
    upload: UploadSection = field(default_factory=UploadSection)
    privacy: PrivacySection = field(default_factory=PrivacySection)

    # ------------------------------------------------------------------ #
    # Load                                                                 #
    # ------------------------------------------------------------------ #

    @classmethod
    def load_or_default(cls, path: Path | None = None) -> ClientConfig:
        """Read from disk, falling back to defaults for any missing field/section.

        Missing file → returns the all-defaults config (caller decides whether
        to `.save()` it). Missing section → defaults for that section only.
        """
        path = path or _DEFAULT_PATH
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))

        server_data = data.get("server", {})
        device_data = data.get("device", {})
        outbox_data = data.get("outbox", {})
        upload_data = data.get("upload", {})
        privacy_data = data.get("privacy", {})

        return cls(
            server=ServerSection(
                url=server_data.get("url", ServerSection.url),
                auth_token=server_data.get("auth_token", ServerSection.auth_token),
            ),
            device=DeviceSection(
                id=device_data.get("id", DeviceSection.id),
                name=device_data.get("name", DeviceSection.name),
                description=device_data.get("description", DeviceSection.description),
            ),
            outbox=OutboxSection(
                root_dir=Path(outbox_data["root_dir"])
                if "root_dir" in outbox_data
                else _DEFAULT_OUTBOX_DIR
            ),
            upload=UploadSection(max_kbps=int(upload_data.get("max_kbps", 0))),
            privacy=PrivacySection(mode=str(privacy_data.get("mode", "off"))),
        )

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    def ensure_device_id(self) -> str:
        """Mint a UUID for `device.id` if empty. Returns the (possibly new) id."""
        if not self.device.id:
            self.device.id = str(uuid.uuid4())
        return self.device.id

    def save(self, path: Path | None = None) -> Path:
        """Write the current config out to `path` (default location if None).

        Parent directory is created if missing. Hand-formats TOML for the
        fixed schema — no tomli-w dependency.
        """
        path = path or _DEFAULT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_toml(), encoding="utf-8")
        return path

    def _render_toml(self) -> str:
        def _kv(key: str, value: str | int) -> str:
            if isinstance(value, int):
                return f"{key} = {value}"
            # Escape backslashes + quotes for safety; client.toml values are
            # short and ASCII-typical so this is enough.
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            return f'{key} = "{escaped}"'

        lines = [
            "[server]",
            _kv("url", self.server.url),
            _kv("auth_token", self.server.auth_token),
            "",
            "[device]",
            _kv("id", self.device.id),
            _kv("name", self.device.name),
            _kv("description", self.device.description),
            "",
            "[outbox]",
            _kv("root_dir", str(self.outbox.root_dir)),
            "",
            "[upload]",
            _kv("max_kbps", self.upload.max_kbps),
            "",
            "[privacy]",
            _kv("mode", self.privacy.mode),
            "",
        ]
        return "\n".join(lines)
