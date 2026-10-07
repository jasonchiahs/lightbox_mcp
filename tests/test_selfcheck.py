"""Start-up self-check (selfcheck.run): what it connects, returns and prints."""

import logging

import pytest

from lightbox_mcp import config, selfcheck
from lightbox_mcp.ccxl import CcxlLightBank
from lightbox_mcp.lights import LightBank

NO_REPLY = "lights never reply, so the lamp itself cannot be confirmed"


class FakeController:
    def __init__(self, code=0):
        self.connected, self.port, self.code = False, None, code

    def connect(self, port):
        self.connected = self.code == 0
        self.port = (port or "auto-scan") if self.connected else None
        return self.code

    def disconnect(self):
        self.connected = False


@pytest.fixture
def lines(caplog):
    caplog.set_level(logging.INFO, logger="lightbox.check")

    def collect():
        # The serial-port inventory depends on the PC running the test.
        return [
            (r.status, r.getMessage())
            for r in caplog.records
            if r.name == "lightbox.check" and "erial ports on this PC" not in r.getMessage()
        ]

    return collect


def loop_lights():
    bank = LightBank(config.load(None))
    for name in config.LIGHTS:
        bank.get(name).open("loop://")
    return bank


def test_connected_rail_and_serial_lights(fake_rail, lines):
    rail, bank = fake_rail(), loop_lights()
    try:
        result = selfcheck.run(rail, bank, bank.cfg, "192.168.1.1")
    finally:
        bank.close_all()
    assert rail.calls == [("connect", "192.168.1.1")]
    assert result["rail_ok"] and result["rail_error"] is None
    assert result["lights"] == {"top": "port open", "up": "port open", "down": "port open"}
    assert result["light_backend"] == "LHM serial protocol (DeviceVersion V2)"
    assert result["realtime"]["rotation_angle_0_360"] == 58.8
    assert lines() == [
        ("info", "=" * 22 + " device check " + "=" * 22),
        ("ok", "[ OK ] Rail/turntable PLC 192.168.1.1: connected, registers readable"),
        ("info", "         Rail position      : 812.50 mm"),
        ("info", "         Rotation position  : 725818.80 deg (= 58.80 deg within one turn)"),
        ("info", "         Rail velocity      : 100.00"),
        ("info", "         Rail ready / busy  : yes / no"),
        ("info", "         Rotation ready/busy: yes / no"),
        ("info", "         E-stop pressed     : no"),
        *[
            ("ok", f"[ OK ] {name:>4} light: loop:// open (9600 8N1, address {address}); {NO_REPLY}")
            for name, address in (("top", 1), ("up", 2), ("down", 3))
        ],
        ("ok", "[ OK ] STATUS: CONNECTED (rail PLC + lights)"),
        ("info", "=" * 58),
    ]


def test_faults_are_flagged_red(fake_rail, lines):
    rail = fake_rail(estop=True, limit_error=True, driver_error=True)
    rail.connected, rail.ip = True, "192.168.1.1"
    bank = LightBank(config.load(None))
    result = selfcheck.run(rail, bank, bank.cfg)
    assert rail.calls == []  # already connected: no reconnect
    assert result["lights"] == {"top": "not configured", "up": "not configured", "down": "not configured"}
    fails = [text for status, text in lines() if status == "fail"]
    assert fails == [
        "[FAIL]   E-stop pressed     : yes",
        "[FAIL]   rail limit error: yes",
        "[FAIL]   rail driver error: yes",
        "[FAIL]   rotation driver error: yes",
    ]
    warns = [text for status, text in lines() if status == "warn"]
    assert warns == [f"[SKIP] {n:>4} light: no COM port configured (LHMconfig.ini [Connect])" for n in config.LIGHTS]
    assert ("ok", "[ OK ] STATUS: RAIL CONNECTED  (lights not connected)") in lines()


def test_unreachable_rail_and_missing_ports(fake_rail, lines, tmp_path):
    ini = tmp_path / "LHMconfig.ini"
    ini.write_text("[DeviceInfo]\nDeviceVersion=V2\n[Connect]\nTopComName=COM97\n", encoding="utf-8")
    cfg = config.load(ini)
    rail, bank = fake_rail(connect_code=1), LightBank(cfg)
    result = selfcheck.run(rail, bank, cfg, "10.1.2.3")
    assert result["rail_ok"] is False and result["rail_error"] == "code 1: message for code 1"
    assert result["realtime"] is None
    assert result["lights"]["top"] == "COM97 not present"
    assert lines()[1:4] == [
        ("fail", "[FAIL] Rail/turntable PLC 10.1.2.3: NOT connected (code 1: message for code 1)"),
        ("info", "         Check the USB cable / power and that the USB-Ethernet adapter is set to 192.168.1.x"),
        ("fail", "[FAIL]  top light: COM97 does not exist on this PC"),
    ]
    assert lines()[-2] == ("fail", "[FAIL] STATUS: NOT CONNECTED")


def test_ccxl_controller(fake_rail, lines):
    bank = CcxlLightBank(config.load(None))
    bank.ctl = FakeController()
    result = selfcheck.run(fake_rail(connect_code=1), bank, bank.cfg)
    assert result["lights"] == {n: "controller answered" for n in config.LIGHTS}
    assert result["light_backend"] == "CCXL (CCXL_calibrate.dll, DeviceVersion V1.x)"
    assert (
        "ok",
        "[ OK ] Light controller: answered on auto-scan via CCXL_calibrate.dll (top/up/down = address 1/2/3)",
    ) in lines()
    assert lines()[-2] == ("fail", "[FAIL] STATUS: LIGHTS CONNECTED, RAIL NOT CONNECTED")


def test_ccxl_controller_silent(fake_rail, lines):
    bank = CcxlLightBank(config.load(None))
    bank.ctl = FakeController(code=7)
    result = selfcheck.run(fake_rail(connect_code=1), bank, bank.cfg)
    error = "Light controller did not answer on any COM port (auto-scan) (InitAndConnect code 7)"
    assert result["lights"] == {n: error for n in config.LIGHTS}
    assert ("fail", f"[FAIL] Light controller: {error}") in lines()
