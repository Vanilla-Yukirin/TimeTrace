[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'This test uses the real per-user installation paths on a disposable GitHub-hosted Windows runner.'
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$DistRoot = Join-Path $RepoRoot 'dist\windows'
$Setup = Join-Path $DistRoot 'TimeTrace-Client-Setup-x64.exe'
$AppDir = Join-Path $env:LOCALAPPDATA 'Programs\TimeTrace'
$AppExe = Join-Path $AppDir 'TimeTrace Client.exe'
$AppRuntime = Join-Path $AppDir '_internal'
$Dashboard = Join-Path $AppRuntime 'timetrace\client\dashboard'
$BrandIcon = Join-Path $Dashboard 'timetrace-kuro.ico'
$DataDir = Join-Path $env:USERPROFILE 'TimeTraceData'
$MenuDir = Join-Path ([Environment]::GetFolderPath('Programs')) 'TimeTrace'
$MenuShortcut = Join-Path $MenuDir 'TimeTrace Client.lnk'
$LegacyShortcut = Join-Path $MenuDir 'Uninstall TimeTrace.lnk'
$StartupShortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'TimeTrace Client.lnk'
$Registration = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VanillaYukirin.TimeTrace.Client_is1'
$Shell = New-Object -ComObject WScript.Shell

foreach ($Path in @($AppDir, $DataDir, $MenuDir, $StartupShortcut, $Registration)) {
    if (Test-Path -LiteralPath $Path) { throw "Expected a clean test profile: $Path exists" }
}

function Invoke-Installer([string]$Exe, [string]$Scenario, [string]$Options = '') {
    $Log = Join-Path $env:RUNNER_TEMP "timetrace-$Scenario.log"
    # The native uninstaller respawns from a temporary directory.
    $Process = Start-Process -FilePath $Exe -WindowStyle Hidden -Wait -PassThru `
        -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG=`"$Log`" $Options"
    if ($Process.ExitCode -ne 0) {
        Get-Content -LiteralPath $Log -Tail 50 -ErrorAction SilentlyContinue
        throw "Installer failed ($Scenario): $($Process.ExitCode)"
    }
    Write-Host "Passed: $Scenario"
}

function Assert-Shortcut([string]$Path, [string]$Arguments) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing shortcut: $Path" }
    $Shortcut = $Shell.CreateShortcut($Path)
    if ($Shortcut.TargetPath -ne $AppExe -or [string]$Shortcut.Arguments -ne $Arguments) {
        throw "Unexpected shortcut target or arguments: $Path"
    }
    if ($Shortcut.IconLocation -ne "$BrandIcon,0") {
        throw "Unexpected shortcut icon: $Path"
    }
}

function Remove-TestInstall([string]$Scenario) {
    $Uninstaller = (Get-ItemProperty -LiteralPath $Registration).UninstallString.Trim('"')
    Invoke-Installer $Uninstaller $Scenario
    foreach ($Path in @($AppExe, $AppRuntime, $MenuShortcut, $StartupShortcut, $Registration)) {
        if (Test-Path -LiteralPath $Path) { throw "Uninstall left $Path" }
    }
}

Invoke-Installer $Setup 'fresh-install'
Assert-Shortcut $MenuShortcut '--gui'
Assert-Shortcut $StartupShortcut ''
$BuiltExe = Join-Path $DistRoot 'TimeTrace Client\TimeTrace Client.exe'
if ((Get-FileHash -LiteralPath $AppExe).Hash -ne (Get-FileHash -LiteralPath $BuiltExe).Hash) {
    throw 'Installed executable differs from the build output'
}
foreach ($File in @('index.html', 'app.js', 'style.css', 'timetrace-kuro.ico')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Dashboard $File) -PathType Leaf)) {
        throw "Installed dashboard is missing $File"
    }
}
Remove-TestInstall 'fresh-uninstall'

# Recreate the layout of the ZIP installer with sign-in startup disabled.
New-Item -ItemType Directory -Path $AppDir, $MenuDir, (Join-Path $DataDir 'outbox') -Force | Out-Null
$LegacyFiles = @(
    (Join-Path $AppDir 'Install TimeTrace.ps1'),
    (Join-Path $AppDir 'Uninstall TimeTrace.ps1'),
    $LegacyShortcut
)
foreach ($Path in @($AppExe) + $LegacyFiles) { Set-Content -LiteralPath $Path 'legacy package fixture' }
$Config = Join-Path $DataDir 'client.toml'
$Outbox = Join-Path $DataDir 'outbox\state.json'
Set-Content -LiteralPath $Config '# existing configuration'
Set-Content -LiteralPath $Outbox '{"acked":0}'

Invoke-Installer $Setup 'legacy-upgrade'
Assert-Shortcut $MenuShortcut '--gui'
foreach ($Path in @($StartupShortcut) + $LegacyFiles) {
    if (Test-Path -LiteralPath $Path) { throw "Upgrade retained $Path" }
}
Invoke-Installer $Setup 'enable-startup' '/TASKS=autostart'
Assert-Shortcut $StartupShortcut ''
Invoke-Installer $Setup 'preserve-startup'
Assert-Shortcut $StartupShortcut ''
Remove-TestInstall 'legacy-uninstall'
if ((Get-Content -LiteralPath $Config -Raw).Trim() -ne '# existing configuration' -or
    (Get-Content -LiteralPath $Outbox -Raw).Trim() -ne '{"acked":0}') {
    throw 'Upgrade or uninstall changed the existing client data'
}
Write-Host 'Verified Windows install, upgrade, startup selection, uninstall and data retention.'
