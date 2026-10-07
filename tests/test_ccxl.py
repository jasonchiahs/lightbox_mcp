"""CCXL backend (DeviceVersion V1.x): SW_MLB's call sequence and dataPram_New layout, without hardware."""

import ctypes

from lightbox_mcp import config, protocol
from lightbox_mcp.ccxl import CcxlLightBank, DataPramNew


class FakeController:
    def __init__(self):
        self.connected, self.port, self.calls = False, None, []

    def connect(self, port):
        self.calls.append(("connect", port))
        self.connected, self.port = True, port or "auto-scan"
        return 0

    def disconnect(self):
        self.calls.append(("disconnect",))
        self.connected = False

    def light_on_off(self, on, address):
        self.calls.append(("setlightOnOff", on, address))
        return 0

    def set_level(self, param, address):
        self.calls.append(("SetDataLevel", bytes(param), address))
        return 0


def make_bank():
    bank = CcxlLightBank(config.load(None))
    bank.ctl = FakeController()
    return bank


def test_struct_layout_matches_sw_mlb():
    # Offsets written by CCXL_V5_Product::SetLightParam (packed, 0x19 bytes).
    offsets = {name: getattr(DataPramNew, name).offset for name, _ in DataPramNew._fields_}
    assert offsets == {"ill": 0, "cct": 2, "eight": 4, "nine": 6, "hz": 8, "duty": 0xC, "mode": 0xE,
                       "flikerLevel": 0xF, "highlevel": 0x11, "lowLevel": 0x13, "changehz": 0x15}
    assert ctypes.sizeof(DataPramNew) == 0x19


def test_white_sends_on_then_level_with_one_connection():
    bank = make_bank()
    for name in ("top", "up", "down"):
        bank.get(name).open(None)
    assert bank.ctl.calls == [("connect", None)]  # one auto-scan for all three lights

    bank.set_param("top", protocol.MODE_WHITE, 5000, 5000)
    on, level = bank.ctl.calls[1:]
    assert on == ("setlightOnOff", True, 1)
    param = DataPramNew.from_buffer_copy(level[1])
    assert (param.ill, param.cct, param.mode, level[2]) == (5000, 5000, b"\x01", 1)
    assert bank.get("top").last_command["mode"] == 1


def test_flicker_uses_flicker_level_and_float_hz():
    bank = make_bank()
    bank.get("up").open("COM4")
    bank.set_param("up", protocol.MODE_FLICKER, 3000, 5000, frequency=60, duty=50)
    param = DataPramNew.from_buffer_copy(bank.ctl.calls[-1][1])
    assert (param.hz, param.duty, param.flikerLevel, param.mode, bank.ctl.calls[-1][2]) == (60.0, 50, 3000, b"\x03", 2)


def test_off_and_capabilities():
    bank = make_bank()
    bank.get("down").open(None)
    bank.off("down")
    assert bank.ctl.calls[-1] == ("setlightOnOff", False, 3)
    assert bank.supports("up", protocol.MODE_INFRARED) and not bank.supports("top", protocol.MODE_FLICKER)
    bank.close_all()
    assert bank.ctl.calls[-1] == ("disconnect",)
