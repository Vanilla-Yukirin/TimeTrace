[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) {
    throw 'The TimeTrace capture client can only be packaged on Windows.'
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$BuildRoot = Join-Path $RepoRoot 'build\windows'
$DistRoot = Join-Path $RepoRoot 'dist\windows'

Push-Location $RepoRoot
try {
    uv sync --group dev
    uv run python packaging/windows/build_assets.py --output $BuildRoot
    uv run pyinstaller packaging/windows/timetrace-client.spec --noconfirm --clean `
        --distpath $DistRoot --workpath (Join-Path $BuildRoot 'pyinstaller')

    $PackageDir = Join-Path $DistRoot 'TimeTrace Client'
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDir 'TimeTrace Client.exe'))) {
        throw "Packaged executable was not created under $PackageDir"
    }
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'scripts\windows\install-client.ps1') `
        -Destination (Join-Path $PackageDir 'Install TimeTrace.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'scripts\windows\uninstall-client.ps1') `
        -Destination (Join-Path $PackageDir 'Uninstall TimeTrace.ps1') -Force

    $ZipPath = Join-Path $DistRoot 'TimeTrace-Client-windows-x64.zip'
    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -CompressionLevel Optimal
    Write-Host "Built: $PackageDir"
    Write-Host "Archive: $ZipPath"
}
finally {
    Pop-Location
}
