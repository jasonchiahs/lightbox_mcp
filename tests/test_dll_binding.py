"""How RailController and CcxlController bind the vendor DLLs' C++ exports, without the DLLs.

The real export tables are read from src/lightbox_mcp/vendor/ (pure PE parsing); the
library itself is replaced by a fake that records every call.
"""

import ctypes
from ctypes import POINTER, c_bool, c_double, c_int, c_void_p

import pytest

from lightbox_mcp import ccxl, native, rail
from lightbox_mcp.errors import DeviceError

RAIL_CLASS = "LHMGuideV2@ModbusApi"


class FakeFunction:
    def __init__(self, lib, name):
        self.lib, self.name = lib, name
        self.argtypes, self.restype = None, None

    def __call__(self, *args):
        self.lib.calls.append((self.name, args))
        effect = self.lib.effects.get(self.name)
        return effect(*args) if effect else 0


class FakeLib:
    def __init__(self):
        self.functions, self.calls, self.effects = {}, [], {}

    def __getitem__(self, mangled):
        return self.functions.setdefault(mangled, FakeFunction(self, mangled))


@pytest.fixture
def fake_lib(monkeypatch):
    lib = FakeLib()
    monkeypatch.setattr(native, "load", lambda name: lib)
    return lib


def rail_exports():
    names = native.exported_names(native.VENDOR_DIR / "LHMGuideV2.dll")
    return {name: native.find_method(names, name, RAIL_CLASS) for name in rail._SIGNATURES}


def test_export_tables_resolve():
    assert len(native.exported_names(native.VENDOR_DIR / "LHMGuideV2.dll")) == 37
    ccxl_names = native.exported_names(native.VENDOR_DIR / "CCXL_calibrate.dll")
    assert len(ccxl_names) == 81
    assert all(mangled in ccxl_names for mangled, _, _ in ccxl._METHODS.values())
    assert f"??0{ccxl.CLS}@@QEAA@XZ" in ccxl_names
    exports = rail_exports()
    statics = {name for name, (_, member) in exports.items() if not member}
    assert statics == {"GetCodeMessage", "GetMoveVelocityRange", "GetMovePositionRange", "GetRotateSpeedRange"}
    with pytest.raises(LookupError, match="LHMGuideV2::Nope is not exported"):
        native.find_method(native.exported_names(native.VENDOR_DIR / "LHMGuideV2.dll"), "Nope", RAIL_CLASS)


def test_rail_binds_argtypes_and_passes_this_to_members(fake_lib):
    exports = rail_exports()
    controller = rail.RailController()

    def position(this, axis, out):
        out._obj.value = 812.5 if axis == rail.AXIS_RAIL else 90.0
        return 0

    def position_range(axis, lo, hi):
        lo._obj.value, hi._obj.value = 0.0, 1750.0

    fake_lib.effects[exports["GetCurrentPosition"][0]] = position
    fake_lib.effects[exports["GetMovePositionRange"][0]] = position_range

    assert controller.connect("192.168.1.1") == 0
    assert controller.connected and controller.ip == "192.168.1.1"
    assert controller.position(rail.AXIS_RAIL) == 812.5
    assert controller.position(rail.AXIS_ROTATE) == 90.0
    assert controller.position_range() == (0.0, 1750.0)
    controller.start_move_to(100.0)
    controller.disconnect()
    assert not controller.connected and controller.ip is None

    for name, (argtypes, restype) in rail._SIGNATURES.items():
        mangled, member = exports[name]
        fn = fake_lib.functions[mangled]  # every signature is resolved on first use
        assert fn.argtypes == ([c_void_p] if member else []) + argtypes, name
        assert fn.restype == restype, name

    by_name = {mangled: name for name, (mangled, _) in exports.items()}
    shown = [(by_name[mangled], args) for mangled, args in fake_lib.calls]
    assert [name for name, _ in shown] == [
        "InitAndConnect",
        "GetCurrentPosition",
        "GetCurrentPosition",
        "GetMovePositionRange",
        "SetMovePositionVal",
        "StartMove",
        "Disconnect",
    ]
    this = controller._this
    assert shown[0][1] == (this, b"192.168.1.1")
    assert shown[1][1][:2] == (this, rail.AXIS_RAIL)
    assert shown[3][1][0] == rail.AXIS_RAIL  # static: no `this`
    assert shown[4][1] == (this, rail.AXIS_RAIL, 100.0)
    assert shown[5][1] == (this, rail.AXIS_RAIL)
    assert shown[6][1] == (this,)


def test_rail_errors_and_messages(fake_lib):
    exports = rail_exports()
    controller = rail.RailController()
    with pytest.raises(DeviceError, match="Rail is not connected; call rail_connect first."):
        controller.ready(rail.AXIS_RAIL)

    fake_lib.effects[exports["GetCodeMessage"][0]] = lambda code: (
        b"Error! Input Value Out Of Range." if code == 4 else None
    )
    assert controller.message(4) == "Error! Input Value Out Of Range."
    assert controller.message(5) == "invalid axis index for this function"
    assert controller.message(9) == "unknown status"

    controller.connect("192.168.1.1")
    fake_lib.effects[exports["SetMoveVelocity"][0]] = lambda *args: 4
    with pytest.raises(rail.RailError) as info:
        controller.set_velocity(99999)
    assert str(info.value) == "SetMoveVelocity failed with code 4: Error! Input Value Out Of Range."
    assert (info.value.func, info.value.code) == ("SetMoveVelocity", 4)


def test_rail_bool_readings(fake_lib):
    exports = rail_exports()
    controller = rail.RailController()
    controller.connect("192.168.1.1")

    def pressed(this, out):
        out._obj.value = True
        return 0

    fake_lib.effects[exports["GetScramButtonSignal"][0]] = pressed
    assert controller.estop_pressed() is True
    assert controller.busy(rail.AXIS_ROTATE) is False
    name, args = fake_lib.calls[-1]
    assert name == exports["GetBusySignal"][0] and args[:2] == (controller._this, rail.AXIS_ROTATE)
    assert isinstance(args[2]._obj, c_bool)


def test_ccxl_binds_methods_with_this(fake_lib):
    controller = ccxl.CcxlController()
    assert controller.connect(None) == 0
    assert (controller.connected, controller.port) == (True, "auto-scan")
    ctor = f"??0{ccxl.CLS}@@QEAA@XZ"
    assert fake_lib.calls[0] == (ctor, (controller._obj,))
    assert fake_lib[ctor].argtypes == [c_void_p] and fake_lib[ctor].restype == c_void_p
    assert ctypes.sizeof(controller._obj) == ccxl.OBJECT_SIZE

    assert controller.connect("com4") == 0
    assert controller.port == "COM4"
    assert controller.light_on_off(True, 2) == 0
    param = ccxl.DataPramNew(ill=5000, cct=6500, mode=b"\x01")
    assert controller.set_level(param, 3) == 0
    controller.disconnect()
    assert (controller.connected, controller.port) == (False, None)

    m = {key: mangled for key, (mangled, _, _) in ccxl._METHODS.items()}
    names = [name for name, _ in fake_lib.calls]
    assert names == [
        ctor,
        m["InitAndConnect"],
        m["InitAndConnectCom"],
        m["setlightOnOff"],
        m["SetDataLevel"],
        m["Disconnectport"],
    ]
    args = [a for _, a in fake_lib.calls]
    assert args[1] == (controller._obj,)
    assert args[2] == (controller._obj, 4)
    assert args[3][:2] == (controller._obj, True) and list(args[3][2]) == [2] and args[3][3] == 1
    assert args[4][0] is controller._obj and args[4][1]._obj is param and list(args[4][2]) == [3]
    assert args[5] == (controller._obj,)

    for key, (mangled, argtypes, restype) in ccxl._METHODS.items():
        assert fake_lib[mangled].argtypes == [c_void_p] + argtypes, key
        assert fake_lib[mangled].restype == restype, key
    assert ccxl._METHODS["setlightOnOff"][1] == [c_bool, POINTER(c_int), c_int]
    assert ccxl._METHODS["SetDataLevel"][1][0] == POINTER(ccxl.DataPramNew)


def test_ccxl_rejects_non_com_names_and_failed_connects(fake_lib):
    controller = ccxl.CcxlController()
    with pytest.raises(DeviceError, match=r"'loop://' is not a COM port name \(expected e.g. COM4\)"):
        controller.connect("loop://")
    fake_lib.effects[ccxl._METHODS["InitAndConnect"][0]] = lambda this: 3
    assert controller.connect(None) == 3
    assert (controller.connected, controller.port) == (False, None)


def test_dll_load_failure_is_a_device_error(monkeypatch):
    def fail(name):
        raise OSError("The vendor DLLs need 64-bit Python on Windows.")

    monkeypatch.setattr(native, "load", fail)
    with pytest.raises(DeviceError, match="Cannot load LHMGuideV2.dll: The vendor DLLs need"):
        rail.RailController().position_range()
    with pytest.raises(DeviceError, match="Cannot load CCXL_calibrate.dll: The vendor DLLs need"):
        ccxl.CcxlController().connect(None)


def test_double_readings_use_c_double(fake_lib):
    exports = rail_exports()
    controller = rail.RailController()
    controller.connect("192.168.1.1")
    controller.velocity()
    name, args = fake_lib.calls[-1]
    assert name == exports["GetCurrentMoveVelocity"][0]
    assert isinstance(args[2]._obj, c_double)
