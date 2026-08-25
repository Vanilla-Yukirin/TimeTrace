[CmdletBinding()]
param(
    [string]$SourcePath = '',
    [string]$InstallPath = (Join-Path $env:LOCALAPPDATA 'Programs\TimeTrace'),
    [switch]$NoAutostart,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'

if (-not $SourcePath) {
    $BundledExe = Join-Path $PSScriptRoot 'TimeTrace Client.exe'
    $SourcePath = if (Test-Path -LiteralPath $BundledExe) {
        $PSScriptRoot
    }
    else {
        Join-Path $PSScriptRoot '..\..\dist\windows\TimeTrace Client'
    }
}

function Resolve-FullPath([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path)
}

function Stop-TimeTraceClient {
    $Live = @(Get-Process -Name 'TimeTrace Client','timetrace-client' -ErrorAction SilentlyContinue)
    if (-not $Live) { return }

    $Ports = [System.Collections.Generic.HashSet[int]]::new()
    [void]$Ports.Add(8764)
    try {
        $ProcessIds = @($Live | Select-Object -ExpandProperty Id)
        Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object {
                $_.OwningProcess -in $ProcessIds -and
                $_.LocalAddress -in @('127.0.0.1', '::1')
            } |
            ForEach-Object { [void]$Ports.Add([int]$_.LocalPort) }
    }
    catch {
        # Get-NetTCPConnection can be unavailable on older Windows builds.
        # The documented default port remains the first graceful attempt.
    }

    foreach ($Port in $Ports) {
        try {
            $Origin = "http://127.0.0.1:$Port"
            $Session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
            $Page = Invoke-WebRequest -Uri "$Origin/" -WebSession $Session -TimeoutSec 2
            $Match = [regex]::Match($Page.Content, 'name="timetrace-csrf" content="([^"]+)"')
            if (-not $Match.Success) { continue }
            $Headers = @{
                Origin = $Origin
                'X-TimeTrace-CSRF' = $Match.Groups[1].Value
            }
            Invoke-RestMethod -Uri "$Origin/api/shutdown" -Method Post `
                -WebSession $Session -Headers $Headers -ContentType 'application/json' -Body '{}' `
                -TimeoutSec 2 | Out-Null
            break
        }
        catch {
            # Try the next loopback listener owned by this client process.
        }
    }

    $Deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        $Live = @(Get-Process -Name 'TimeTrace Client','timetrace-client' -ErrorAction SilentlyContinue)
        if (-not $Live) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)

    # A disabled/broken local UI must not make in-place updates impossible.
    # Force-stop only after every graceful loopback endpoint and the shutdown
    # grace period have failed; the durable Outbox preserves queued work.
    $Live | Stop-Process -Force
    $ForceDeadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $Live = @(Get-Process -Name 'TimeTrace Client','timetrace-client' -ErrorAction SilentlyContinue)
        if (-not $Live) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $ForceDeadline)
    throw 'TimeTrace is still running after graceful and forced shutdown attempts.'
}

$Source = Resolve-FullPath $SourcePath
$Target = Resolve-FullPath $InstallPath
$ExpectedRoot = Resolve-FullPath (Join-Path $env:LOCALAPPDATA 'Programs')
if (-not $Target.StartsWith($ExpectedRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "InstallPath must remain under $ExpectedRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $Source 'TimeTrace Client.exe'))) {
    throw "SourcePath does not contain TimeTrace Client.exe: $Source"
}

Stop-TimeTraceClient

$Parent = Split-Path -Parent $Target
[System.IO.Directory]::CreateDirectory($Parent) | Out-Null
$Staging = Join-Path $Parent ('.TimeTrace.staging-' + $PID)
$Backup = Join-Path $Parent ('.TimeTrace.backup-' + $PID)
if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }

try {
    Copy-Item -LiteralPath $Source -Destination $Staging -Recurse
    if (Test-Path -LiteralPath $Target) {
        Move-Item -LiteralPath $Target -Destination $Backup
    }
    Move-Item -LiteralPath $Staging -Destination $Target

    $StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TimeTrace'
    [System.IO.Directory]::CreateDirectory($StartMenu) | Out-Null
    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut((Join-Path $StartMenu 'TimeTrace Client.lnk'))
    $Shortcut.TargetPath = Join-Path $Target 'TimeTrace Client.exe'
    $Shortcut.WorkingDirectory = $Target
    $Shortcut.IconLocation = (Join-Path $Target 'TimeTrace Client.exe') + ',0'
    $Shortcut.Description = 'TimeTrace Windows capture client'
    $Shortcut.Save()

    $Startup = [Environment]::GetFolderPath('Startup')
    $StartupShortcut = Join-Path $Startup 'TimeTrace Client.lnk'
    if ($NoAutostart) {
        if (Test-Path -LiteralPath $StartupShortcut) {
            Remove-Item -LiteralPath $StartupShortcut -Force
        }
    }
    else {
        [System.IO.Directory]::CreateDirectory($Startup) | Out-Null
        $Autostart = $Shell.CreateShortcut($StartupShortcut)
        $Autostart.TargetPath = Join-Path $Target 'TimeTrace Client.exe'
        $Autostart.WorkingDirectory = $Target
        $Autostart.IconLocation = (Join-Path $Target 'TimeTrace Client.exe') + ',0'
        $Autostart.Description = 'Start TimeTrace when this user signs in'
        $Autostart.Save()
    }

    $Uninstall = $Shell.CreateShortcut((Join-Path $StartMenu 'Uninstall TimeTrace.lnk'))
    $PowerShell = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $PowerShell) { $PowerShell = (Get-Command powershell).Source }
    $Uninstall.TargetPath = $PowerShell
    $Uninstall.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + `
        (Join-Path $Target 'Uninstall TimeTrace.ps1') + '"'
    $Uninstall.WorkingDirectory = $Parent
    $Uninstall.IconLocation = (Join-Path $Target 'TimeTrace Client.exe') + ',0'
    $Uninstall.Save()

    if (Test-Path -LiteralPath $Backup) { Remove-Item -LiteralPath $Backup -Recurse -Force }
}
catch {
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
    if (Test-Path -LiteralPath $Backup) { Move-Item -LiteralPath $Backup -Destination $Target }
    throw
}
finally {
    if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
}

if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $Target 'TimeTrace Client.exe') `
        -WorkingDirectory $Target -WindowStyle Hidden
}

Write-Host "Installed TimeTrace Client to $Target"
Write-Host 'Existing configuration, Outbox, and screenshots under TimeTraceData were preserved.'
if ($NoAutostart) {
    Write-Host 'Automatic startup is disabled for this installation.'
}
else {
    Write-Host 'TimeTrace Client will start automatically when this user signs in.'
}
