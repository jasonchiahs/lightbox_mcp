"""ctypes binding for SW_MLB's LHMGuideV2.dll (rail + turntable, Modbus/TCP to the PLC).

The DLL exports only the C++ class ModbusApi::LHMGuideV2; its methods are
resolved by name from the export table and called with a `this` pointer.
The class keeps no state of its own (its constructor is empty), so a small
buffer serves as the object. SW_MLB always connects to 192.168.1.1, which
the controller exposes over its USB-Ethernet adapter.

Status codes: 0 success, 1 PLC connection failed, 2 read failed, 3 write failed,
4 value out of range, 5 wrong axis index.
"""

from __future__ import annotations

import ctypes
import functools
import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from ctypes import POINTER, c_bool, c_char_p, c_double, c_int

from . import logs, native
from .errors import DeviceError

AXIS_RAIL = 0
AXIS_ROTATE = 1
AXES = {"rail": AXIS_RAIL, "rotate": AXIS_ROTATE}
CODE_READ_FAILED = 2
CODE_WRITE_FAILED = 3

_STATUS_TEXT = {
    0: "success",
    1: "PLC connection failed",
    2: "register read failed",
    3: "register write failed",
    4: "input value out of range",
    5: "invalid axis index for this function",
}

BoolP = POINTER(c_bool)
DoubleP = POINTER(c_double)

_SIGNATURES: dict[str, tuple[list, object]] = {
    "GetCodeMessage": ([c_int], c_char_p),
    "InitAndConnect": ([c_char_p], c_int),
    "Disconnect": ([], None),
    "ClearHistoryAlarm": ([], c_int),
    "ScramReset": ([], c_int),
    "GetScramButtonSignal": ([BoolP], c_int),
    "GetReadySignal": ([c_int, BoolP], c_int),
    "GetBusySignal": ([c_int, BoolP], c_int),
    "GetCurrentPosition": ([c_int, DoubleP], c_int),
    "GetCurrentMoveVelocity": ([c_int, DoubleP], c_int),
    "GetMoveVelocityRange": ([c_int, DoubleP, DoubleP], None),
    "SetMoveVelocity": ([c_int, c_double], c_int),
    "SetMoveVelocitySet": ([c_int], c_int),
    "GetMovePositionRange": ([c_int, DoubleP, DoubleP], None),
    "SetMovePositionVal": ([c_int, c_double], c_int),
    "StartMove": ([c_int], c_int),
    "StopMove": ([c_int], c_int),
    "SetZeroPoint": ([c_int], c_int),
    "GetHomeDone": ([BoolP], c_int),
    "GetLimitError": ([c_int, BoolP], c_int),
    "GetDriverError": ([c_int, BoolP], c_int),
    "GetRotateSpeedRange": ([DoubleP, DoubleP], None),
    "StartForwardRotate": ([], c_int),
    "StartBackwardRotate": ([], c_int),
    "StopRotateMove": ([], c_int),
    "SetForwardOrBackwardSpeed": ([c_double], c_int),
    "SetForwardOrBackwardSpeedSet": ([], c_int),
    "SetSwingSpeed": ([c_double], c_int),
    "GetSwingSpeedRange": ([DoubleP, DoubleP], None),
    "StartSwingRotate": ([], c_int),
}


class RailError(DeviceError):
    def __init__(self, func: str, code: int, message: str) -> None:
        super().__init__(f"{func} failed with code {code}: {message}")
        self.func, self.code, self.message = func, code, message


class RailController:
    """Thread-safe facade over LHMGuideV2.dll."""

    def __init__(self) -> None:
        self._lib: ctypes.CDLL | None = None
        self._fns: dict[str, Callable] = {}
        self._this = ctypes.create_string_buffer(64)
        self._lock = threading.Lock()
        self.connected = False
        self.ip: str | None = None
        self._trace_level = logging.INFO

    @contextmanager
    def polling(self):
        """Log the status reads of a wait loop at DEBUG so they don't flood the console."""
        self._trace_level = logging.DEBUG
        try:
            yield
        finally:
            self._trace_level = logging.INFO

    def _dll(self) -> ctypes.CDLL:
        if self._lib is None:
            try:
                lib = native.load("LHMGuideV2.dll")
            except OSError as exc:
                raise DeviceError(f"Cannot load LHMGuideV2.dll: {exc}") from exc
            names = native.exported_names(native.vendor_dir() / "LHMGuideV2.dll")
            fns = {}
            for name, (argtypes, restype) in _SIGNATURES.items():
                mangled, member = native.find_method(names, name, "LHMGuideV2@ModbusApi")
                fn = lib[mangled]
                fn.argtypes = ([ctypes.c_void_p] if member else []) + argtypes
                fn.restype = restype
                # Instance methods get the object as their hidden first argument.
                fns[name] = functools.partial(fn, self._this) if member else fn
            self._fns = fns
            self._lib = lib
        return self._lib

    def _fn(self, name: str) -> Callable:
        self._dll()
        return self._fns[name]

    def message(self, code: int) -> str:
        raw = self._fn("GetCodeMessage")(code)
        if raw:
            for enc in ("utf-8", "gbk"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    pass
        return _STATUS_TEXT.get(code, "unknown status")

    def _raw(self, name: str, *args, require_connection: bool = True):
        if require_connection and not self.connected:
            raise DeviceError("Rail is not connected; call rail_connect first.")
        fn = self._fn(name)
        with self._lock:
            return logs.traced(logs.rail_log, name, fn, args, self._trace_level)

    def _check(self, name: str, *args) -> None:
        code = self._raw(name, *args)
        if code in (CODE_READ_FAILED, CODE_WRITE_FAILED) and self.ip:
            # The PLC drops idle Modbus/TCP connections and the DLL does not notice:
            # every later read/write then fails with 2/3. Reconnect once and retry.
            logs.rail_log.warning("%s failed with code %d; reconnecting to %s and retrying", name, code, self.ip)
            ip = self.ip
            self.disconnect()
            if self.connect(ip) == 0:
                code = self._raw(name, *args)
        if code != 0:
            raise RailError(name, code, self.message(code))

    def _get_bool(self, name: str, *args) -> bool:
        value = c_bool()
        self._check(name, *args, ctypes.byref(value))
        return value.value

    def _get_double(self, name: str, *args) -> float:
        value = c_double()
        self._check(name, *args, ctypes.byref(value))
        return value.value

    def _range(self, name: str, *args) -> tuple[float, float]:
        lo, hi = c_double(), c_double()
        self._raw(name, *args, ctypes.byref(lo), ctypes.byref(hi), require_connection=False)
        return lo.value, hi.value

    # --- connection ---------------------------------------------------------
    def connect(self, ip: str) -> int:
        code = self._raw("InitAndConnect", ip.encode("ascii"), require_connection=False)
        self.connected = code == 0
        self.ip = ip if self.connected else None
        return code

    def disconnect(self) -> None:
        if self._lib is not None:
            with self._lock:
                logs.traced(logs.rail_log, "Disconnect", self._fns["Disconnect"], ())
        self.connected = False
        self.ip = None

    # --- status ---------------------------------------------------------------
    def ready(self, axis: int) -> bool:
        return self._get_bool("GetReadySignal", axis)

    def busy(self, axis: int) -> bool:
        return self._get_bool("GetBusySignal", axis)

    def position(self, axis: int = AXIS_RAIL) -> float:
        return self._get_double("GetCurrentPosition", axis)

    def velocity(self, axis: int = AXIS_RAIL) -> float:
        return self._get_double("GetCurrentMoveVelocity", axis)

    def limit_error(self, axis: int = AXIS_RAIL) -> bool:
        return self._get_bool("GetLimitError", axis)

    def driver_error(self, axis: int) -> bool:
        return self._get_bool("GetDriverError", axis)

    def estop_pressed(self) -> bool:
        return self._get_bool("GetScramButtonSignal")

    def homing(self) -> bool:
        """True while homing is in progress, False once it is done."""
        return self._get_bool("GetHomeDone")

    # --- ranges (static, no connection needed) -------------------------------
    def velocity_range(self, axis: int = AXIS_RAIL) -> tuple[float, float]:
        return self._range("GetMoveVelocityRange", axis)

    def position_range(self, axis: int = AXIS_RAIL) -> tuple[float, float]:
        return self._range("GetMovePositionRange", axis)

    def rotate_speed_range(self) -> tuple[float, float]:
        return self._range("GetRotateSpeedRange")

    def swing_speed_range(self) -> tuple[float, float]:
        return self._range("GetSwingSpeedRange")

    # --- rail motion ----------------------------------------------------------
    def set_velocity(self, value: float) -> None:
        self._check("SetMoveVelocity", AXIS_RAIL, value)
        self._check("SetMoveVelocitySet", AXIS_RAIL)

    def start_move_to(self, position: float) -> None:
        self._check("SetMovePositionVal", AXIS_RAIL, position)
        self._check("StartMove", AXIS_RAIL)

    def stop(self) -> None:
        self._check("StopMove", AXIS_RAIL)

    def home(self, axis: int) -> None:
        self._check("SetZeroPoint", axis)

    def reset_estop(self) -> None:
        self._check("ScramReset")

    def clear_alarm_history(self) -> None:
        self._check("ClearHistoryAlarm")

    # --- turntable --------------------------------------------------------------
    def set_rotate_speed(self, value: float) -> None:
        self._check("SetForwardOrBackwardSpeed", value)
        self._check("SetForwardOrBackwardSpeedSet")

    def set_swing_speed(self, value: float) -> None:
        self._check("SetSwingSpeed", value)
        self._check("SetForwardOrBackwardSpeedSet")

    def rotate(self, forward: bool) -> None:
        self._check("StartForwardRotate" if forward else "StartBackwardRotate")

    def swing(self) -> None:
        self._check("StartSwingRotate")

    def stop_rotate(self) -> None:
        self._check("StopRotateMove")
