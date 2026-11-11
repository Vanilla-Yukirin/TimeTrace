# TimeTrace Windows client package

The Windows x64 setup wizard installs the standalone capture client for the
current user. It includes the Python runtime and installs without administrator
privileges. Client data lives in `%USERPROFILE%\TimeTraceData`.

## Build

Use PowerShell 7 and uv on Windows. From the repository root:

```powershell
pwsh -File scripts/windows/build-client.ps1
```

The build creates:

- `dist/windows/TimeTrace Client/` — unpacked application directory;
- `dist/windows/TimeTrace-Client-Setup-x64.exe` — graphical installer.

The build downloads and verifies a pinned Inno Setup compiler in `build/windows/`.
Pass `-InnoCompiler` with the path to `ISCC.exe` to use a local compiler.
PyInstaller is pinned by `uv.lock`. The executable is windowless, uses the
stable app ID `VanillaYukirin.TimeTrace.Client`, and writes rotating logs to
`%USERPROFILE%\TimeTraceData\logs\client.log`.

## Install or update

Download [TimeTrace-Client-Setup-x64.exe](https://github.com/Vanilla-Yukirin/TimeTrace/releases/latest/download/TimeTrace-Client-Setup-x64.exe)
and double-click it. Setup installs to `%LOCALAPPDATA%\Programs\TimeTrace`,
creates a Start Menu shortcut, and offers to open the dashboard when finished.
The **Start TimeTrace when I sign in** checkbox controls login startup.

For an upgrade, exit TimeTrace from the system tray and close its dashboard
window before running Setup. Setup updates existing installations, including
those from the old ZIP, and carries forward the current Startup shortcut
setting. Configuration, Outbox entries and screenshots stay in `TimeTraceData`.

Open **Start Menu → TimeTrace → TimeTrace Client** or the tray's control panel
item to open the desktop window. The Windows build uses WebView2. The same
dashboard is available at the configured loopback control address.

On the first launch, select **尚未设置** on the **服务端连接** status card.
Enter the server address and device token, test the connection, then select
**保存并重连**. Edit the connection later under **软件设置 → 服务端连接**.

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

## Uninstall

Exit TimeTrace, then open **Windows Settings → Apps → Installed apps** and
uninstall **TimeTrace Client**. This removes the installed program and shortcuts.
`TimeTraceData` is retained.

## GitHub Releases

Pushing a semantic version tag automatically builds the client on GitHub's
Windows runner and publishes a GitHub Release containing:

- `TimeTrace-Client-Setup-x64.exe`
- `TimeTrace-Client-Setup-x64.exe.sha256`

The tag must be exactly `vX.Y.Z`, must point to a commit in `main`, and must
match both `[project].version` in `pyproject.toml` and `timetrace.__version__`.
For example, after merging version `0.2.2` to `main`:

```powershell
git switch main
git pull --ff-only
git tag -a v0.2.2 -m "TimeTrace Client v0.2.2"
git push origin v0.2.2
```

Normal pushes to `main` run CI. The Windows runner tests installation,
upgrade, startup settings, and uninstall on a clean profile. The tag workflow
repeats these checks on the installer it publishes.

For an optional download integrity check, compare the installer's SHA-256
with the companion file:

```powershell
$expected = (Get-Content .\TimeTrace-Client-Setup-x64.exe.sha256).Split()[0]
$actual = (Get-FileHash .\TimeTrace-Client-Setup-x64.exe -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw 'TimeTrace client checksum mismatch' }
```

## Current distribution boundary

The installer is unsigned, so Windows may display a SmartScreen prompt.
The client's **Check for updates** action opens the download page for a newer
release. The user applies the update by running its setup wizard.
