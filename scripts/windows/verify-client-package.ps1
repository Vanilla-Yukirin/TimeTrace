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
$ZipPath = Join-Path $DistRoot 'TimeTrace-Client-windows-x64.zip'
$ChecksumPath = "$ZipPath.sha256"

foreach ($Path in @(
    $ExePath,
    (Join-Path $PackageDir 'Install TimeTrace.ps1'),
    (Join-Path $PackageDir 'Uninstall TimeTrace.ps1'),
    $ZipPath
)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required package file is missing: $Path"
    }
}

$Parts = @($ExpectedVersion.Split('.') | ForEach-Object { [int]$_ })
$ExpectedFileVersion = (($Parts + @(0)) | Select-Object -First 4) -join '.'
$ActualFileVersion = (Get-Item -LiteralPath $ExePath).VersionInfo.ProductVersion.Trim()
if ($ActualFileVersion -ne $ExpectedFileVersion) {
    throw "EXE version $ActualFileVersion does not match expected $ExpectedFileVersion"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
try {
    $EntryNames = [System.Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($Entry in $Archive.Entries) {
        [void]$EntryNames.Add($Entry.FullName.Replace('\', '/'))
    }
    foreach ($RequiredEntry in @(
        'TimeTrace Client/TimeTrace Client.exe',
        'TimeTrace Client/Install TimeTrace.ps1',
        'TimeTrace Client/Uninstall TimeTrace.ps1'
    )) {
        if (-not $EntryNames.Contains($RequiredEntry)) {
            throw "Release archive is missing $RequiredEntry"
        }
    }
}
finally {
    $Archive.Dispose()
}

$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($WriteChecksum) {
    Set-Content -LiteralPath $ChecksumPath `
        -Value "$Hash  TimeTrace-Client-windows-x64.zip" `
        -Encoding ascii
    Write-Host "Checksum: $ChecksumPath"
}

Write-Host "Verified TimeTrace Client $ExpectedVersion"
Write-Host "Archive SHA-256: $Hash"
