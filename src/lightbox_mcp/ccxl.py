"""Lights of MLB-LHM controllers with DeviceVersion V1/V1.1/V1.2 (SW_MLB's CCXL_V5_Product).

SW_MLB picks the light implementation from LHMconfig.ini [DeviceInfo]
DeviceVersion: "V2" -> LHM_V2_Product (lights.py, three serial ports, no
replies); "V1", "V1.1", "V1.2" -> CCXL_V5_Product, which drives all lights
through CCXL_calibrate.dll (class CCXL_CA_V2::CCXL_calibrate_V2):

- InitAndConnect()      scans every COM port and keeps the one whose
                        controller answers the handshake (InitAndConnect(int n) for COMn)
- setlightOnOff(on, &addr, 1)                 then
- SetDataLevel(&dataPram_New, &addr, 1)       for each light change

The controller replies to every command, so a 0 return code means the
light controller acknowledged it.
"""

from __future__ import annotations

import ctypes
import functools
import threading
from ctypes import POINTER, c_bool, c_char, c_float, c_int, c_uint16

from . import logs, native, protocol
from .backend import timestamp
from .config import LIGHTS, LhmConfig, com_port_number
from .errors import DeviceError, light_not_connected

DLL = "CCXL_calibrate.dll"
CLS = "CCXL_calibrate_V2@CCXL_CA_V2"
# SW_MLB allocates 0x419 bytes for the object; leave generous room.
OBJECT_SIZE = 4096
# Port label while connected through InitAndConnect()'s scan of every COM port.
AUTO_SCAN = "auto-scan"


class DataPramNew(ctypes.Structure):
    """dataPram_New, as filled by SW_MLB's CCXL_V5_Product::SetLightParam (packed, 25 bytes)."""

    _pack_ = 1
    _fields_ = [
        ("ill", c_uint16),
        ("cct", c_uint16),
        ("eight", c_uint16),  # 850 nm level
        ("nine", c_uint16),  # 940 nm level
        ("hz", c_float),  # flicker frequency
        ("duty", c_uint16),
        ("mode", c_char),  # 1 white, 2 infrared, 3 flicker
        ("flikerLevel", c_uint16),  # illuminance while flickering
        ("highlevel", c_uint16),
        ("lowLevel", c_uint16),
        ("changehz", c_float),
    ]


assert ctypes.sizeof(DataPramNew) == 25

IntP = POINTER(c_int)
_METHODS = {
    # name: (mangled, argtypes after `this`, restype)
    "InitAndConnect": (f"?InitAndConnect@{CLS}@@QEAAHXZ", [], c_int),
    "InitAndConnectCom": (f"?InitAndConnect@{CLS}@@QEAAHH@Z", [c_int], c_int),
    "Disconnectport": (f"?Disconnectport@{CLS}@@QEAAXXZ", [], None),
    "setlightOnOff": (f"?setlightOnOff@{CLS}@@QEAAH_NPEAHH@Z", [c_bool, IntP, c_int], c_int),
    "SetDataLevel": (
        f"?SetDataLevel@{CLS}@@QEAAHPEAUdataPram_New@@PEAHH@Z",
        [POINTER(DataPramNew), IntP, c_int],
        c_int,
    ),
}


class CcxlController:
    """One CCXL_calibrate_V2 object = one connection to the light controller."""

    def __init__(self) -> None:
        self._lib = None
        self._fns: dict[str, object] = {}
        self._obj = None
        self._lock = threading.Lock()
        self.connected = False
        self.port: str | None = None

    def _load(self) -> None:
        """Load the DLL, bind the methods and construct the C++ object."""
        try:
            self._lib = native.load(DLL)
        except OSError as exc:
            raise DeviceError(f"Cannot load {DLL}: {exc}") from exc
        for key, (mangled, argtypes, restype) in _METHODS.items():
            fn = self._lib[mangled]
            fn.argtypes, fn.restype = [ctypes.c_void_p, *argtypes], restype
            self._fns[key] = fn
        ctor = self._lib[f"??0{CLS}@@QEAA@XZ"]
        ctor.argtypes, ctor.restype = [ctypes.c_void_p], ctypes.c_void_p
        self._obj = ctypes.create_string_buffer(OBJECT_SIZE)
        ctor(self._obj)

    def _call(self, name: str, *args):
        if self._lib is None:
            self._load()
        method = functools.partial(self._fns[name], self._obj)
        with self._lock:
            return logs.traced(logs.light_log, name, method, args)

    def connect(self, port: str | None) -> int:
        """port 'COMn' -> InitAndConnect(n); None -> scan all ports like SW_MLB."""
        if port:
            number = com_port_number(port)
            if number is None:
                raise DeviceError(f"{port!r} is not a COM port name (expected e.g. COM4)")
            code = self._call("InitAndConnectCom", number)
        else:
            code = self._call("InitAndConnect")
        self.connected = code == 0
        self.port = (port.upper() if port else AUTO_SCAN) if self.connected else None
        return code

    def disconnect(self) -> None:
        if self.connected:
            self._call("Disconnectport")
        self.connected = False
        self.port = None

    def light_on_off(self, on: bool, address: int) -> int:
        addr = (c_int * 1)(address)
        return self._call("setlightOnOff", on, addr, 1)

    def set_level(self, param: DataPramNew, address: int) -> int:
        addr = (c_int * 1)(address)
        return self._call("SetDataLevel", ctypes.byref(param), addr, 1)


class CcxlLight:
    """One of top/up/down on the shared controller connection (same interface as lights.Light)."""

    def __init__(self, bank: CcxlLightBank, name: str, address: int) -> None:
        self.bank = bank
        self.name = name
        self.address = address
        self.last_command: dict | None = None
        self.is_open = False

    @property
    def connected(self) -> bool:
        return self.is_open and self.bank.ctl.connected

    @property
    def port(self) -> str | None:
        return self.bank.ctl.port if self.connected else None

    def open(self, port: str | None) -> None:
        self.bank.ensure_connected(port)
        self.is_open = True

    def close(self) -> None:
        self.is_open = False
        self.bank.disconnect_if_unused()


class CcxlLightBank:
    backend = "CCXL (CCXL_calibrate.dll, DeviceVersion V1.x)"

    def __init__(self, cfg: LhmConfig) -> None:
        self.cfg = cfg
        self.ctl = CcxlController()
        self.lights = {n: CcxlLight(self, n, cfg.lights[n].address) for n in LIGHTS}

    def get(self, name: str) -> CcxlLight:
        return self.lights[name]

    def connect_lights(self, port: str | None, ports: dict[str, str | None]) -> dict:
        """One connection serves every light: the first port given, else a scan of all COM ports."""
        chosen = port or next((ports[n] for n in LIGHTS if ports.get(n)), None)
        for name in LIGHTS:
            self.get(name).open(chosen)
        return {"backend": self.backend, "lights": self.status()}

    def ensure_connected(self, port: str | None) -> None:
        if self.ctl.connected:
            if port and self.ctl.port not in (port.upper(), AUTO_SCAN):
                raise DeviceError(f"Light controller already connected on {self.ctl.port}")
            return
        code = self.ctl.connect(port)
        if code != 0:
            where = port or f"any COM port ({AUTO_SCAN})"
            raise DeviceError(f"Light controller did not answer on {where} (InitAndConnect code {code})")

    def disconnect_if_unused(self) -> None:
        if not any(light.is_open for light in self.lights.values()):
            self.ctl.disconnect()

    def status(self) -> dict:
        return {
            name: {
                "connected": light.connected,
                "port": light.port,
                "address": light.address,
                "configured_port": None,
                "last_command": light.last_command,
            }
            for name, light in self.lights.items()
        }

    def close_all(self) -> None:
        for light in self.lights.values():
            light.is_open = False
        self.ctl.disconnect()

    def supports(self, light: str, mode: int) -> bool:
        return not (light == "top" and mode == protocol.MODE_FLICKER)

    def _require(self, light: CcxlLight) -> None:
        if not light.connected:
            raise light_not_connected(light.name)

    def set_param(
        self,
        name: str,
        mode: int,
        illuminance: int,
        cct: int,
        ir_850: int = 0,
        ir_940: int = 0,
        frequency: int = 0,
        duty: int = 1,
    ) -> list[str]:
        light = self.get(name)
        self._require(light)
        param = DataPramNew(
            ill=illuminance,
            cct=cct,
            eight=ir_850,
            nine=ir_940,
            hz=float(frequency),
            duty=duty,
            mode=bytes([mode]),
            flikerLevel=illuminance if mode == protocol.MODE_FLICKER else 0,
            highlevel=0,
            lowLevel=0,
            changehz=0.0,
        )
        # SW_MLB: switch the light on, then send its parameters.
        code = self.ctl.light_on_off(True, light.address)
        if code != 0:
            raise DeviceError(f"setlightOnOff({name}, address {light.address}) returned {code}")
        code = self.ctl.set_level(param, light.address)
        if code != 0:
            raise DeviceError(f"SetDataLevel({name}, address {light.address}) returned {code}")
        params = {
            "illuminance": illuminance,
            "cct": cct,
            "ir_850": ir_850,
            "ir_940": ir_940,
            "frequency": frequency,
            "duty": duty,
        }
        light.last_command = {"mode": mode, **params, "at": timestamp()}
        return [
            f"setlightOnOff(True, [{light.address}], 1)",
            f"SetDataLevel({logs.render(param)}, [{light.address}], 1)",
        ]

    def off(self, name: str) -> list[str]:
        light = self.get(name)
        self._require(light)
        code = self.ctl.light_on_off(False, light.address)
        if code != 0:
            raise DeviceError(f"setlightOnOff(False, {name}) returned {code}")
        light.last_command = {"off": True, "at": timestamp()}
        return [f"setlightOnOff(False, [{light.address}], 1)"]
