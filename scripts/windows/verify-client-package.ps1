[CmdletBinding()]
param(
    [string]$DistRoot = '',
    [Parameter(Mandatory)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$ExpectedVersion,
    [switch]$WriteChecksum
)

$ErrorActionPreference = 'Stop'

if (-not $DistRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $DistRoot = Join-Path $RepoRoot 'dist\windows'
}
$DistRoot = [System.IO.Path]::GetFullPath($DistRoot)
$PackageDir = Join-Path $DistRoot 'TimeTrace Client'
$ExePath = Join-Path $PackageDir 'TimeTrace Client.exe'
$InstallerName = 'TimeTrace-Client-Setup-x64.exe'
$InstallerPath = Join-Path $DistRoot $InstallerName
$ChecksumPath = "$InstallerPath.sha256"

foreach ($Path in @(
    $ExePath,
    $InstallerPath,
    (Join-Path $PackageDir '_internal/timetrace/client/dashboard/index.html'),
    (Join-Path $PackageDir '_internal/timetrace/client/dashboard/app.js'),
    (Join-Path $PackageDir '_internal/timetrace/client/dashboard/style.css'),
    (Join-Path $PackageDir '_internal/timetrace/client/dashboard/timetrace-kuro.ico')
)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required package file is missing: $Path"
    }
}

$ExpectedFileVersion = "$ExpectedVersion.0"
foreach ($Path in @($ExePath, $InstallerPath)) {
    $ActualFileVersion = (Get-Item -LiteralPath $Path).VersionInfo.FileVersion.Trim()
    if ($ActualFileVersion -ne $ExpectedFileVersion) {
        throw "$Path has version $ActualFileVersion, expected $ExpectedFileVersion"
    }
}

$Hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
# Exercise the frozen entry point and its imports without starting capture.
$Smoke = Start-Process -FilePath $ExePath -ArgumentList '--help' -WindowStyle Hidden -PassThru
if (-not $Smoke.WaitForExit(30000)) {
    Stop-Process -Id $Smoke.Id
    throw 'Packaged client --help did not exit within 30 seconds'
}
if ($Smoke.ExitCode -ne 0) {
    throw "Packaged client --help failed: $($Smoke.ExitCode)"
}
if ($WriteChecksum) {
    Set-Content -LiteralPath $ChecksumPath `
        -Value "$Hash  $InstallerName" `
        -Encoding ascii
    Write-Host "Checksum: $ChecksumPath"
}

Write-Host "Verified TimeTrace Client $ExpectedVersion"
Write-Host "Installer SHA-256: $Hash"
