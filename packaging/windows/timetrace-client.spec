# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH).parents[1]
assets = project_root / "build" / "windows"

a = Analysis(
    [str(project_root / "packaging" / "windows" / "client_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        "mss.windows",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pystray._win32",
        "win32timezone",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TimeTrace Client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(assets / "timetrace.ico"),
    version=str(assets / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="TimeTrace Client",
)
