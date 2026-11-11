[CmdletBinding()]
param(
    [string]$InnoCompiler = ''
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) {
    throw 'The TimeTrace capture client can only be packaged on Windows.'
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$BuildRoot = Join-Path $RepoRoot 'build\windows'
$DistRoot = Join-Path $RepoRoot 'dist\windows'

if (-not $InnoCompiler) {
    $CompilerVersion = '6.7.4'
    $CompilerDir = Join-Path $BuildRoot "inno-$CompilerVersion"
    $InnoCompiler = Join-Path $CompilerDir 'ISCC.exe'
    if (-not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
        $null = New-Item -ItemType Directory -Path $BuildRoot -Force
        $Download = Join-Path $BuildRoot "innosetup-$CompilerVersion.exe"
        $CompilerTag = $CompilerVersion.Replace('.', '_')
        $Url = "https://github.com/jrsoftware/issrc/releases/download/is-$CompilerTag/innosetup-$CompilerVersion.exe"
        Invoke-WebRequest -Uri $Url -OutFile $Download -MaximumRetryCount 2 -TimeoutSec 120
        if ((Get-FileHash -LiteralPath $Download -Algorithm SHA256).Hash -ne
            'a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c') {
            throw 'Inno Setup compiler download checksum mismatch'
        }
        $CompilerLog = Join-Path $BuildRoot 'inno-setup.log'
        $Options = "/CURRENTUSER /PORTABLE=1 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=`"$CompilerDir`" /LOG=`"$CompilerLog`""
        $Process = Start-Process -FilePath $Download -ArgumentList $Options -WindowStyle Hidden -Wait -PassThru
        if ($Process.ExitCode -ne 0) {
            Get-Content -LiteralPath $CompilerLog -Tail 30 -ErrorAction SilentlyContinue
            throw "Inno Setup portable installation failed: $($Process.ExitCode)"
        }
    }
}
$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).Path

Push-Location $RepoRoot
try {
    uv sync --locked --group dev
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency install failed' }
    uv run python packaging/windows/build_assets.py --output $BuildRoot
    if ($LASTEXITCODE -ne 0) { throw 'Windows asset generation failed' }
    uv run pyinstaller packaging/windows/timetrace-client.spec --noconfirm --clean `
        --distpath $DistRoot --workpath (Join-Path $BuildRoot 'pyinstaller')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

    $PackageDir = Join-Path $DistRoot 'TimeTrace Client'
    if (-not (Test-Path -LiteralPath (Join-Path $PackageDir 'TimeTrace Client.exe'))) {
        throw "Packaged executable was not created under $PackageDir"
    }
    $Version = (uv run --no-sync python -c "from timetrace import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+$') {
        throw 'Could not determine the client package version'
    }
    $AppIcon = Join-Path $BuildRoot 'timetrace.ico'
    & $InnoCompiler /Qp "/DAppVersion=$Version" "/DPackageDir=$PackageDir" "/DOutputDir=$DistRoot" `
        "/DAppIcon=$AppIcon" 'packaging/windows/timetrace-client.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }
    Write-Host "Built: $PackageDir"
    Write-Host "Installer: $(Join-Path $DistRoot 'TimeTrace-Client-Setup-x64.exe')"
}
finally {
    Pop-Location
}
