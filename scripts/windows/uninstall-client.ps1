[CmdletBinding()]
param(
    [string]$InstallPath = (Join-Path $env:LOCALAPPDATA 'Programs\TimeTrace')
)

$ErrorActionPreference = 'Stop'
$Target = [System.IO.Path]::GetFullPath($InstallPath)
$ExpectedRoot = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
if (-not $Target.StartsWith($ExpectedRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "InstallPath must remain under $ExpectedRoot"
}

Get-Process -Name 'TimeTrace Client' -ErrorAction SilentlyContinue | Stop-Process
$Deadline = [DateTime]::UtcNow.AddSeconds(8)
while ((Get-Process -Name 'TimeTrace Client' -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $Deadline) {
    Start-Sleep -Milliseconds 250
}

$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\TimeTrace'
if (Test-Path -LiteralPath $StartMenu) { Remove-Item -LiteralPath $StartMenu -Recurse -Force }
$StartupShortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'TimeTrace Client.lnk'
if (Test-Path -LiteralPath $StartupShortcut) { Remove-Item -LiteralPath $StartupShortcut -Force }
if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }

Write-Host 'TimeTrace Client was uninstalled.'
Write-Host 'TimeTraceData was preserved. This script never removes configuration, Outbox, or screenshots.'
