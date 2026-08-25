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
the new version. Existing configuration, Outbox entries, and screenshots are
preserved. It also creates a current-user Startup shortcut so capture resumes
after sign-in. Pass `-NoAutostart` when running the installer to opt out.

## Uninstall

Use **Start Menu → TimeTrace → Uninstall TimeTrace**. Uninstall removes only the
program directory, Start Menu shortcuts, and Startup shortcut; it never removes
`TimeTraceData`.

## Current distribution boundary

This package is suitable for local/manual installation. It is not code-signed,
not yet attached to GitHub Releases, and has no automatic updater. Those are
separate release-engineering steps rather than prerequisites for running the
installed client on this machine.
