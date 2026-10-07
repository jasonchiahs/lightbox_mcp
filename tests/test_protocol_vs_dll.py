"""Check protocol.py against the vendor light DLLs, byte for byte.

The DLLs' OpenDevice() passes a 4-character name straight to CreateFileA, so
pointing them at an ordinary file captures every frame they would have sent
to the serial port. The file is opened exclusively, so it is read back only
after the device is closed. Windows x64 only.
"""

import ctypes
import itertools
import os
import random
import struct
import sys
from pathlib import Path

import pytest

from lightbox_mcp import protocol

VENDOR = Path(__file__).resolve().parents[1] / "src" / "lightbox_mcp" / "vendor"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or struct.calcsize("P") != 8, reason="vendor DLLs are Windows x64"
)

C_INT = ctypes.c_int
THIS = ctypes.c_void_p
SAMPLES = [0, 1, 255, 256, 2300, 4095, 5000, 10000, 60000, 65535]
_rng = random.Random(1234)
RANDOM = [(_rng.randrange(256), _rng.randrange(65536), _rng.randrange(65536)) for _ in range(200)]


class Capture:
    """A vendor light object whose 'serial port' is a capture file."""

    NAME = "c.bn"  # exactly 4 characters: opened verbatim, relative to the cwd

    def __init__(self, dll: str, cls: str, directory: Path, ctor: bool) -> None:
        os.add_dll_directory(str(VENDOR))
        self.lib = ctypes.CDLL(str(VENDOR / dll))
        self.cls = cls
        self.obj = ctypes.create_string_buffer(512)
        if ctor:
            self.fn("??0{c}@@QEAA@XZ", [THIS], THIS)(self.obj)
        self.path = directory / self.NAME
        self.path.write_bytes(b"")  # OpenDevice uses OPEN_EXISTING
        self.expected: list[tuple[object, bytes]] = []
        cwd = os.getcwd()
        os.chdir(directory)
        try:
            opened = self.fn("?OpenDevice@{c}@@QEAAHJPEBD@Z", [THIS, ctypes.c_long, ctypes.c_char_p], C_INT)
            assert opened(self.obj, protocol.BAUD_RATE, self.NAME.encode()) == 0
        finally:
            os.chdir(cwd)

    def fn(self, mangled: str, argtypes, restype):
        f = self.lib[mangled.format(c=self.cls)]
        f.argtypes, f.restype = argtypes, restype
        return f

    def call(self, label, fn, args, expected: bytes) -> None:
        assert fn(self.obj, *args) == 0, label
        self.expected.append((label, expected))

    def verify(self) -> int:
        self.fn("?CloseDevice@{c}@@QEAAXXZ", [THIS], None)(self.obj)
        data = self.path.read_bytes()
        assert len(data) == 8 * len(self.expected), (len(data), len(self.expected))
        for i, (label, frame) in enumerate(self.expected):
            got = data[8 * i: 8 * i + 8]
            assert got == frame, f"{label}: DLL sent {got.hex(' ')}, protocol.py builds {frame.hex(' ')}"
        return len(self.expected)


def test_top_light_frames(tmp_path):
    top = Capture("LHM_TopLight.dll", "TopLight@LHM_TopLight", tmp_path, ctor=False)
    lux_kelvin = top.fn("?SetLuxKelvin@{c}@@QEAAHHHH@Z", [THIS, C_INT, C_INT, C_INT], C_INT)
    infrared = top.fn("?SetEightAndNine@{c}@@QEAAHHHH@Z", [THIS, C_INT, C_INT, C_INT], C_INT)
    mode = top.fn("?SetLightMode@{c}@@QEAAHHH@Z", [THIS, C_INT, C_INT], C_INT)

    cases = [(addr, a, b) for addr in (1, 2, 3, 255) for a, b in itertools.product(SAMPLES, repeat=2)] + RANDOM
    for addr, a, b in cases:
        top.call(("SetLuxKelvin", a, b, addr), lux_kelvin, (a, b, addr), protocol.top_lux_kelvin(addr, a, b))
        top.call(("SetEightAndNine", a, b, addr), infrared, (a, b, addr), protocol.top_infrared(addr, a, b))
    for addr, m in itertools.product((1, 2, 3, 200), (0, 1, 2, 3, 255)):
        top.call(("SetLightMode", m, addr), mode, (m, addr), protocol.set_mode(addr, m))
    assert top.verify() > 800


def test_flicker_light_frames(tmp_path):
    fl = Capture("LHM_FlikerLight.dll", "FlikerLight@LHM_FlikerLight", tmp_path, ctor=True)
    lux_kelvin = fl.fn("?SetLuxKelvin@{c}@@QEAAHHHHH@Z", [THIS, C_INT, C_INT, C_INT, C_INT], C_INT)
    freq_duty = fl.fn("?SetFrequencyDuty@{c}@@QEAAHHHHH@Z", [THIS, C_INT, C_INT, C_INT, C_INT], C_INT)
    mode = fl.fn("?SetLightMode@{c}@@QEAAHHH@Z", [THIS, C_INT, C_INT], C_INT)

    cases = [(addr, a, b) for addr in (1, 2, 3) for a, b in itertools.product(SAMPLES, repeat=2)] + RANDOM
    for (addr, a, b), has_cct in itertools.product(cases, (0, 1)):
        fl.call(("SetLuxKelvin", a, b, addr, has_cct), lux_kelvin, (a, b, addr, has_cct),
                protocol.flicker_lux_kelvin(addr, a, b, bool(has_cct)))
        duty = b % 256
        fl.call(("SetFrequencyDuty", a, duty, addr, has_cct), freq_duty, (a, duty, addr, has_cct),
                protocol.flicker_freq_duty(addr, a, duty, bool(has_cct)))
    for addr, m in itertools.product((1, 2, 3), (1, 2, 3)):
        fl.call(("SetLightMode", m, addr), mode, (m, addr), protocol.set_mode(addr, m))
    assert fl.verify() > 1600


def test_describe():
    assert protocol.describe(protocol.top_lux_kelvin(1, 5000, 6500)) == "addr=1 illuminance=5000 cct=6500"
    assert protocol.describe(protocol.flicker_freq_duty(2, 60, 50, has_cct=False)) == "addr=2 frequency=60 duty=50"
    assert protocol.describe(protocol.flicker_freq_duty(2, 60, 50, has_cct=True)) == "addr=2 frequency=60 (duty not sent)"
