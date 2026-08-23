"""ClientConfig — schema + load / save for `client.toml`.

`client.toml` is the persisted configuration the standalone
`timetrace-client` reads at startup. The single-process `uv run timetrace`
entry doesn't need it because in that mode capture is wired via
`InProcessBackend` directly. In the two-process mode, the client process
reads this file to learn:

- **server** — URL + bearer token to talk to a remote `timetrace-server`
- **device** — local identity (UUID + human name + description)
- **outbox** — append-only spool dir for offline buffering
- **upload** — per-second bandwidth cap (token bucket inside OutboxSender)
- **storage** — local data_dir (where capture writes screenshots before they
  reach the server's blob store; reused as outbox blob root if not set)
- **capture** — intervals / idle threshold / capture mode (mirrors
  `common.CaptureConfig`)
- **privacy** — operational privacy + P4 mode selector (mirrors
  `common.PrivacyConfig`)

Default location: `%USERPROFILE%/TimeTraceData/client.toml`.

Read uses stdlib `tomllib` (read-only). Write hand-formats the small fixed
schema rather than pulling in `tomli-w` for one file. The format kept
intentionally flat — primitive keys per section — so the manual writer is
short and a human can hand-edit it without surprises.

Env-var overrides
-----------------
`load_or_default()` reads the file, overlays `TIMETRACE_SERVER_URL`,
`TIMETRACE_AUTH_TOKEN`,
`TIMETRACE_DEVICE_ID`, `TIMETRACE_DEVICE_NAME`, `TIMETRACE_OUTBOX_DIR`,
`TIMETRACE_UPLOAD_MAX_KBPS`, `TIMETRACE_DATA_DIR`, `TIMETRACE_PRIVACY_MODE`
and the other supported environment variables, then validates the effective
configuration. Useful for headless deployments that want to ship a baseline
`client.toml` and tune via env.
"""

from __future__ import annotations

import os
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from timetrace.common.config import CaptureConfig, PrivacyConfig, StorageConfig
from timetrace.common.protocol import DeviceMetadata, validate_device_id

_DEFAULT_PATH = Path.home() / "TimeTraceData" / "client.toml"
_DEFAULT_OUTBOX_DIR = Path.home() / "TimeTraceData" / "outbox"


@dataclass
class EndpointSection:
    """One reachable path to the (single, shared) backend.

    Multiple endpoints = multiple network routes to the SAME box/DB; the client
    uses the first ``enabled`` + healthy one (see EndpointSelector). ``type`` is
    ``"http"`` (plain URL) or ``"ssh"`` (client manages an ``ssh -L`` tunnel and
    talks to ``url`` = the local forward end). The ssh_* fields are ignored for
    http endpoints. See infra/PLAN-MULTIPATH-CLIENT.md.
    """

    name: str = "default"
    url: str = "http://127.0.0.1:8765"
    enabled: bool = True
    type: str = "http"  # "http" | "ssh"
    # SSH tunnel spec (type == "ssh"): equivalent to
    # ssh -N -L <local port from url>:<remote_host>:<remote_port> <ssh_host>
    ssh_host: str = ""
    ssh_port: int = 22
    remote_host: str = "127.0.0.1"
    remote_port: int = 0
    identity_file: str = ""


@dataclass
class ServerSection:
    url: str = "http://127.0.0.1:8765"
    auth_token: str = ""
    # Ordered priority list (first = most preferred). Empty → the legacy single
    # `url` is synthesized into one endpoint, so old client.toml keeps working.
    endpoints: list[EndpointSection] = field(default_factory=list)

    def all_endpoints(self) -> list[EndpointSection]:
        """Configured endpoints, or a single one synthesized from ``url`` when
        none are declared (backward compatibility with the old single-url form)."""
        if self.endpoints:
            return self.endpoints
        return [EndpointSection(name="default", url=self.url)]

    def enabled_endpoints(self) -> list[EndpointSection]:
        """``all_endpoints`` filtered to enabled, preserving priority order."""
        return [e for e in self.all_endpoints() if e.enabled]


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
    # 0 means unlimited; positive ints are KB/s caps that OutboxSender turns
    # into a token bucket.
    max_kbps: int = 0
    # Drop screenshot entries whose image blob exceeds this many MB (0 disables
    # the cap). Default 2MB protects the remote (HK) path, where large legacy
    # PNGs can't upload reliably and wedge the strict-FIFO queue. Bump it (or
    # set 0) for a fast LAN-direct session where big blobs upload fine.
    max_image_mb: float = 2.0
    # Concurrent in-flight uploads (sliding-window sender). 1 = strict serial
    # (default, unchanged). >1 hides per-request latency on slow/high-latency
    # links while preserving record→screenshot→close order + in-order ack.
    concurrency: int = 1


@dataclass
class ClientConfig:
    server: ServerSection = field(default_factory=ServerSection)
    device: DeviceSection = field(default_factory=DeviceSection)
    outbox: OutboxSection = field(default_factory=OutboxSection)
    upload: UploadSection = field(default_factory=UploadSection)
    storage: StorageConfig = field(default_factory=StorageConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)

    # ------------------------------------------------------------------ #
    # Load                                                                 #
    # ------------------------------------------------------------------ #

    @classmethod
    def load_or_default(cls, path: Path | None = None) -> ClientConfig:
        """Load and validate the effective file -> environment configuration.

        Missing file → returns the all-defaults config (caller decides whether
        to `.save()` it). Missing section → defaults for that section only.

        File values are deliberately not validated before environment
        overrides are applied: a deployment may keep a placeholder or stale
        value in ``client.toml`` and supply the valid host identity through
        ``TIMETRACE_DEVICE_*``. Ordinary callers use this method so they cannot
        accidentally forget either the override layer or final validation.
        """
        path = path or _DEFAULT_PATH
        config = cls._load_file_or_default(path)
        config.apply_env_overrides(validate=False)
        config.validate_device_identity(
            source=f"effective client configuration ({path} + TIMETRACE_* environment)"
        )
        return config

    @classmethod
    def _load_file_or_default(cls, path: Path) -> ClientConfig:
        """Parse only the file layer without validation (internal use only)."""
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))

        server_data = data.get("server", {})
        device_data = data.get("device", {})
        outbox_data = data.get("outbox", {})
        upload_data = data.get("upload", {})
        storage_data = data.get("storage", {})
        capture_data = data.get("capture", {})
        privacy_data = data.get("privacy", {})

        endpoints = [
            EndpointSection(
                name=str(e.get("name", EndpointSection.name)),
                url=str(e.get("url", EndpointSection.url)),
                enabled=bool(e.get("enabled", True)),
                type=str(e.get("type", "http")),
                ssh_host=str(e.get("ssh_host", "")),
                ssh_port=int(e.get("ssh_port", 22)),
                remote_host=str(e.get("remote_host", "127.0.0.1")),
                remote_port=int(e.get("remote_port", 0)),
                identity_file=str(e.get("identity_file", "")),
            )
            for e in server_data.get("endpoints", [])
        ]

        return cls(
            server=ServerSection(
                url=server_data.get("url", ServerSection.url),
                auth_token=server_data.get("auth_token", ServerSection.auth_token),
                endpoints=endpoints,
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
            upload=UploadSection(
                max_kbps=int(upload_data.get("max_kbps", 0)),
                max_image_mb=float(upload_data.get("max_image_mb", UploadSection.max_image_mb)),
                concurrency=int(upload_data.get("concurrency", UploadSection.concurrency)),
            ),
            storage=_storage_from_toml(storage_data),
            capture=_capture_from_toml(capture_data),
            privacy=_privacy_from_toml(privacy_data),
        )

    # ------------------------------------------------------------------ #
    # Env-var overrides                                                    #
    # ------------------------------------------------------------------ #

    def apply_env_overrides(self, *, validate: bool = True) -> ClientConfig:
        """Overlay TIMETRACE_* env vars on top of current values, in place.

        Returns self so callers can chain. Env vars beat file values; absent
        vars leave the field untouched. Designed for headless deploys: ship a
        baseline `client.toml`, tune per-host via systemd `Environment=`.
        """
        if v := os.getenv("TIMETRACE_SERVER_URL"):
            self.server.url = v
        if v := os.getenv("TIMETRACE_AUTH_TOKEN"):
            self.server.auth_token = v
        if v := os.getenv("TIMETRACE_DEVICE_ID"):
            self.device.id = v
        if v := os.getenv("TIMETRACE_DEVICE_NAME"):
            self.device.name = v
        if v := os.getenv("TIMETRACE_DEVICE_DESC"):
            self.device.description = v
        if v := os.getenv("TIMETRACE_OUTBOX_DIR"):
            self.outbox.root_dir = Path(v)
        if v := os.getenv("TIMETRACE_UPLOAD_MAX_KBPS"):
            self.upload.max_kbps = int(v)
        if v := os.getenv("TIMETRACE_DATA_DIR"):
            self.storage.data_dir = Path(v)
        if v := os.getenv("TIMETRACE_PRIVACY_MODE"):
            self.privacy.mode = v
        if validate:
            self.validate_device_identity(source="TIMETRACE_* environment overrides")
        return self

    def validate_device_identity(
        self,
        *,
        source: str = "client configuration",
        client_version: str = "",
        capabilities: tuple[str, ...] = (),
    ) -> DeviceMetadata:
        """Validate the exact identity contract the server will receive.

        This deliberately raises instead of truncating a user-provided label.
        A strict-FIFO outbox cannot make progress past a server-side 422, so a
        local, field-specific startup error is both safer and more actionable.
        """
        if self.device.id:
            try:
                validate_device_id(self.device.id)
            except ValueError as exc:
                raise ValueError(
                    f"{source} has invalid device.id: {exc}. "
                    "The non-empty ID was not rewritten because queued records may already "
                    "belong to it; correct client.toml or TIMETRACE_DEVICE_ID explicitly."
                ) from exc
        try:
            return DeviceMetadata(
                name=self.device.name,
                description=self.device.description,
                client_version=client_version,
                capabilities=list(capabilities),
            )
        except ValidationError as exc:
            details = "; ".join(
                f"device.{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise ValueError(
                f"{source} has invalid device metadata: {details}. "
                "Correct client.toml or TIMETRACE_DEVICE_* and restart the client."
            ) from exc

    # ------------------------------------------------------------------ #
    # Save                                                                 #
    # ------------------------------------------------------------------ #

    def ensure_device_id(self) -> str:
        """Mint a UUID for `device.id` if empty. Returns the (possibly new) id."""
        if not self.device.id:
            self.device.id = str(uuid.uuid4())
        else:
            try:
                validate_device_id(self.device.id)
            except ValueError as exc:
                raise ValueError(
                    f"configured device.id is invalid: {exc}. Refusing to replace a non-empty "
                    "ID because existing outbox entries may already belong to it."
                ) from exc
        return self.device.id

    def save(self, path: Path | None = None) -> Path:
        """Write the current config out to `path` (default location if None).

        Parent directory is created if missing. Hand-formats TOML for the
        fixed schema — no tomli-w dependency.
        """
        path = path or _DEFAULT_PATH
        self.validate_device_identity(source=str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_toml(), encoding="utf-8")
        return path

    def _render_toml(self) -> str:
        lines = [
            "[server]",
            _kv("url", self.server.url),
            _kv("auth_token", self.server.auth_token),
            "",
        ]
        for ep in self.server.endpoints:
            lines.append("[[server.endpoints]]")
            lines.append(_kv("name", ep.name))
            lines.append(_kv("url", ep.url))
            lines.append(_kv("enabled", ep.enabled))
            lines.append(_kv("type", ep.type))
            if ep.type == "ssh":
                lines.append(_kv("ssh_host", ep.ssh_host))
                lines.append(_kv("ssh_port", ep.ssh_port))
                lines.append(_kv("remote_host", ep.remote_host))
                lines.append(_kv("remote_port", ep.remote_port))
                if ep.identity_file:
                    lines.append(_kv("identity_file", ep.identity_file))
            lines.append("")
        lines += [
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
            _kv("max_image_mb", self.upload.max_image_mb),
            _kv("concurrency", self.upload.concurrency),
            "",
            "[storage]",
            _kv("data_dir", str(self.storage.data_dir)),
            "",
            "[capture]",
            _kv("min_capture_interval_s", self.capture.min_capture_interval_s),
            _kv("max_capture_interval_s", self.capture.max_capture_interval_s),
            _kv("idle_threshold_s", self.capture.idle_threshold_s),
            _kv("switch_capture_delay_s", self.capture.switch_capture_delay_s),
            _kv("capture_mode", self.capture.capture_mode),
            "",
            "[privacy]",
            _kv("mode", self.privacy.mode),
            _kv("paused", self.privacy.paused),
            _kv("store_images", self.privacy.store_images),
            _kv_list("app_blacklist", self.privacy.app_blacklist),
            _kv_list("title_keywords", self.privacy.title_keywords),
            "",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------- #
# Section parsers + writers                                              #
# --------------------------------------------------------------------- #


def _storage_from_toml(data: dict) -> StorageConfig:
    if "data_dir" in data:
        return StorageConfig(data_dir=Path(data["data_dir"]))
    return StorageConfig()


def _capture_from_toml(data: dict) -> CaptureConfig:
    return CaptureConfig(
        min_capture_interval_s=float(
            data.get("min_capture_interval_s", CaptureConfig.min_capture_interval_s)
        ),
        max_capture_interval_s=float(
            data.get("max_capture_interval_s", CaptureConfig.max_capture_interval_s)
        ),
        idle_threshold_s=float(data.get("idle_threshold_s", CaptureConfig.idle_threshold_s)),
        switch_capture_delay_s=float(
            data.get("switch_capture_delay_s", CaptureConfig.switch_capture_delay_s)
        ),
        capture_mode=str(data.get("capture_mode", CaptureConfig.capture_mode)),
    )


def _privacy_from_toml(data: dict) -> PrivacyConfig:
    return PrivacyConfig(
        paused=bool(data.get("paused", False)),
        app_blacklist=list(data.get("app_blacklist", [])),
        title_keywords=list(data.get("title_keywords", [])),
        store_images=bool(data.get("store_images", True)),
        mode=str(data.get("mode", "off")),
    )


def _kv(key: str, value: str | int | float | bool) -> str:
    """Render one TOML key/value pair for a primitive scalar."""
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key} = "{escaped}"'


def _kv_list(key: str, values: list[str]) -> str:
    """Render a list-of-strings TOML key/value pair."""
    if not values:
        return f"{key} = []"
    items = ", ".join('"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values)
    return f"{key} = [{items}]"
