"""Locating and loading the vendor DLLs, and resolving their C++ exports."""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).parent / "vendor"
_dll_dir_handle = None


def vendor_dir() -> Path:
    """Directory with LHMGuideV2.dll, DMT.dll and the debug C runtime DMT.dll needs.

    Overridable with LIGHTBOX_VENDOR_DIR (e.g. a newer SW_MLB install).
    """
    return Path(os.environ.get("LIGHTBOX_VENDOR_DIR") or VENDOR_DIR)


def load(name: str) -> ctypes.CDLL:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise OSError("The vendor DLLs need 64-bit Python on Windows.")
    global _dll_dir_handle
    directory = vendor_dir()
    path = directory / name
    if not path.is_file():
        raise FileNotFoundError(f"{name} not found in {directory}")
    if _dll_dir_handle is None:
        # LHMGuideV2.dll -> DMT.dll -> vcruntime140d.dll / ucrtbased.dll, all in this directory.
        _dll_dir_handle = os.add_dll_directory(str(directory))
    return ctypes.CDLL(str(path))


def exported_names(path: Path) -> list[str]:
    """Names in a PE file's export table."""
    data = path.read_bytes()

    def u16(pos: int) -> int:
        return struct.unpack_from("<H", data, pos)[0]

    def u32(pos: int) -> int:
        return struct.unpack_from("<I", data, pos)[0]

    pe = u32(0x3C)  # PE header
    section_count = u16(pe + 6)
    optional_size = u16(pe + 20)
    optional = pe + 24
    pe32 = u16(optional) == 0x10B  # else PE32+ (64-bit)
    export_rva = u32(optional + (96 if pe32 else 112))  # data directory 0: export table
    # Section headers: (virtual size, virtual address, raw size, raw offset).
    sections = [struct.unpack_from("<IIII", data, optional + optional_size + i * 40 + 8) for i in range(section_count)]

    def offset(rva: int) -> int:
        for vsize, va, rawsize, raw in sections:
            if va <= rva < va + max(vsize, rawsize):
                return rva - va + raw
        raise ValueError(f"RVA {rva:#x} outside the sections of {path}")

    if not export_rva:
        return []
    export_dir = offset(export_rva)
    name_count, name_table = u32(export_dir + 24), offset(u32(export_dir + 32))
    names = []
    for i in range(name_count):
        start = offset(u32(name_table + 4 * i))
        names.append(data[start : data.index(b"\0", start)].decode("ascii"))
    return names


def find_method(names: list[str], method: str, cls: str) -> tuple[str, bool]:
    """MSVC-mangled export for cls::method -> (mangled name, takes `this`).

    '?Method@Class@Ns@@Q...' is an instance method (this in rcx);
    '...@@S...' a static method and '?Method@Ns@@Y...' a free function.
    """
    for mangled in names:
        if mangled.startswith(f"?{method}@{cls}@@"):
            access = mangled[len(f"?{method}@{cls}@@")]
            return mangled, access in "QIA"  # public / protected / private instance
    namespace = cls.split("@", 1)[1]
    for mangled in names:
        if mangled.startswith(f"?{method}@{namespace}@@Y"):
            return mangled, False
    raise LookupError(f"{cls.split('@')[0]}::{method} is not exported")
