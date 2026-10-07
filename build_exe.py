"""Build dist/lightbox_mcp.exe (single file, console window, x64) with PyInstaller.

    uv sync --group build
    uv run --group build python build_exe.py

Bundles Python, the MCP server and src/lightbox_mcp/vendor/ (the SW_MLB V2.7
DLLs plus the debug C runtime that the vendor's Debug-built DMT.dll needs,
as shipped in YANDING_SW_MLB_V2.7.rar) and src/lightbox_mcp/LHMconfig.ini
as the embedded default device configuration.
"""

import struct
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).parent
VENDOR = ROOT / "src" / "lightbox_mcp" / "vendor"

if struct.calcsize("P") != 8:
    sys.exit("Build with 64-bit Python: the vendor DLLs are x64.")

# Each option stays on one line with its value.
# fmt: off
args = [
    str(ROOT / "packaging" / "entry.py"),
    "--name", "lightbox_mcp",
    "--onefile",
    "--console",
    "--noconfirm",
    "--clean",
    "--paths", str(ROOT / "src"),
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    # uvicorn and pyserial pick implementations by name at runtime.
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "serial",
    "--exclude-module", "mcp.cli",
]
# fmt: on
for dll in sorted(VENDOR.glob("*.dll")):
    args += ["--add-binary", f"{dll};lightbox_mcp/vendor"]
# Default device configuration; LHMconfig.ini next to the exe overrides it at run time.
args += ["--add-data", f"{ROOT / 'src' / 'lightbox_mcp' / 'LHMconfig.ini'};lightbox_mcp"]

PyInstaller.__main__.run(args)
