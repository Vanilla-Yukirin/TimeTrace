# TimeTrace Windows client package

This is a current-user, portable-directory installation of the standalone
capture client. It does not install Python, require administrator privileges,
or move `%USERPROFILE%\TimeTraceData`.

## Build

From a PowerShell 7 prompt at the repository root:

```powershell
pwsh -File scripts/windows/build-client.ps1
```

The build creates:

- `dist/windows/TimeTrace Client/` — unpacked application directory;
- `dist/windows/TimeTrace-Client-windows-x64.zip` — distributable archive.

PyInstaller is pinned by `uv.lock`. The executable is windowless, uses the
stable app ID `VanillaYukirin.TimeTrace.Client`, and writes rotating logs to
`%USERPROFILE%\TimeTraceData\logs\client.log`.

## Install or update

Extract the ZIP and run `Install TimeTrace.ps1`, or install the local build:

```powershell
pwsh -File scripts/windows/install-client.ps1
```

The script gracefully stops a running client, atomically replaces
`%LOCALAPPDATA%\Programs\TimeTrace`, creates Start Menu shortcuts, and launches
the dashboard. Existing configuration, Outbox entries, and screenshots are
preserved. On a first install, pass `-NoAutostart` to opt out of launching on
sign-in. Upgrades preserve the existing Startup shortcut preference.

Open **Start Menu → TimeTrace → TimeTrace Client** or the tray's control panel
item to open the desktop window. The Windows build uses WebView2. The same
dashboard is available at the configured loopback control address.

- **状态** shows the active connection, pending image count, capture strategy
  and a live activity feed. Scrolling to the bottom loads older events.
- **截图配置** applies changes as soon as they are saved. Interval mode captures
  the foreground window on a fixed cadence. Smart mode uses switch delay,
  minimum and maximum capture intervals, and optional exact pixel deduplication.
  Both modes respect pause and the existing privacy filters.
- Choose JPG, PNG or WebP. Quality applies to JPG and WebP. Smart compression
  limits the long side to 2560 px and can lower quality or resolution to fit
  the configured upload size. Frames that still exceed the limit are reported
  in the feed.
- **软件设置** offers system, light and dark appearance, a sign-in startup toggle
  and an on-demand GitHub Release check with a download link.

Each new screenshot carries its own metadata in a single ingest. Existing
Outbox entries remain readable and replayable. Activity intervals use only
the screenshot-backed observations made by the standalone recorder.

Updates discover a non-default loopback control port from the running client
process before requesting graceful shutdown. If the control UI is disabled or
unreachable, the installer waits for the grace period and only then uses a
bounded process-stop fallback so manual updates cannot become permanently
blocked.

## Uninstall

Use **Start Menu → TimeTrace → Uninstall TimeTrace**. Uninstall removes only the
program directory, Start Menu shortcuts, and Startup shortcut; it never removes
`TimeTraceData`.

## GitHub Releases

Pushing a semantic version tag automatically builds the client on GitHub's
Windows runner and publishes a GitHub Release containing:

- `TimeTrace-Client-windows-x64.zip`;
- `TimeTrace-Client-windows-x64.zip.sha256`.

The tag must be exactly `vX.Y.Z`, must point to a commit in `main`, and must
match both `[project].version` in `pyproject.toml` and `timetrace.__version__`.
For example, after merging version `0.1.0` to `main`:

```powershell
git switch main
git pull --ff-only
git tag -a v0.1.0 -m "TimeTrace Client v0.1.0"
git push origin v0.1.0
```

Normal pushes to `main` do not publish a client release. Pull requests build
and verify the Windows package in CI so the tag workflow is not the first time
the installer path runs.

On another authorized machine, download both release assets and verify the ZIP
before extracting it:

```powershell
$expected = (Get-Content .\TimeTrace-Client-windows-x64.zip.sha256).Split()[0]
$actual = (Get-FileHash .\TimeTrace-Client-windows-x64.zip -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw 'TimeTrace client checksum mismatch' }
```

## Current distribution boundary

GitHub Releases automate packaging and distribution, but the executable is not
code-signed and the installed client has no automatic updater. Users still
download a release and run the bundled atomic installer manually. Code signing
and in-app update checks remain separate release-engineering steps.
