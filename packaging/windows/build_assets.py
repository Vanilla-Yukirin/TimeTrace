"""Generate deterministic Windows resources used by PyInstaller."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from timetrace import __version__
from timetrace.client.windows_runtime import APP_ICON_PATH


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APP_ICON_PATH, args.output / "timetrace.ico")
    version = tuple(int(part) for part in __version__.split("."))
    padded = version + (0,) * (4 - len(version))
    dotted = ".".join(str(part) for part in padded)
    (args.output / "version_info.txt").write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={padded}, prodvers={padded}, mask=0x3f, flags=0x0,
    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Vanilla-Yukirin'),
    StringStruct('FileDescription', 'TimeTrace Windows capture client'),
    StringStruct('FileVersion', '{dotted}'),
    StringStruct('InternalName', 'TimeTrace Client'),
    StringStruct('OriginalFilename', 'TimeTrace Client.exe'),
    StringStruct('ProductName', 'TimeTrace Client'),
    StringStruct('ProductVersion', '{dotted}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)\n""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
