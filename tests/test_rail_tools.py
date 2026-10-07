"""Rail and turntable MCP tools against a fake rail: arguments, safety checks, waits and replies."""

import asyncio
import time

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from lightbox_mcp import server


@pytest.fixture
def use_rail(monkeypatch, fake_rail):
    def install(**kwargs):
        rail = fake_rail(**kwargs)
        monkeypatch.setattr(server, "rail", rail)
        return rail

    return install


def test_move_waits_until_idle(use_rail):
    rail = use_rail(busy=[True, True, False], position=500.0)
    result = asyncio.run(server.rail_move_to(position=500, velocity=200))
    assert result == {"ok": True, "target": 500, "position": 500.0}
    assert rail.calls == [("set_velocity", 200), ("start_move_to", 500)]


def test_move_without_wait(use_rail):
    rail = use_rail()
    result = asyncio.run(server.rail_move_to(position=10, wait=False))
    assert result == {"ok": True, "started": True, "target": 10}
    assert rail.calls == [("start_move_to", 10)]


@pytest.mark.parametrize(
    ("kwargs", "readings", "message"),
    [
        ({"position": 2000}, {}, r"position 2000 is outside the allowed range \[0.0, 1750.0\]"),
        ({"position": 5, "velocity": 0.5}, {}, r"velocity 0.5 is outside the allowed range \[1.0, 3000.0\]"),
        ({"position": 5}, {"estop": True}, "Emergency stop is pressed; release it and call rail_reset_alarms."),
        ({"position": 5}, {"ready": False}, "Rail axis is not ready; check rail_get_status / rail_reset_alarms."),
    ],
)
def test_move_is_refused(use_rail, kwargs, readings, message):
    rail = use_rail(**readings)
    with pytest.raises(ToolError, match=message):
        asyncio.run(server.rail_move_to(**kwargs))
    assert rail.calls == []


def test_move_fault_stops_the_rail(use_rail):
    rail = use_rail(busy=[True] * 5, limit_error=True)
    with pytest.raises(ToolError, match="Rail reported a limit/driver error during the move; stopped."):
        asyncio.run(server.rail_move_to(position=5))
    assert rail.calls[-1] == ("stop",)


def test_move_timeout_stops_the_rail(use_rail):
    rail = use_rail(busy=[True] * 100, position=42.0)
    t0 = time.monotonic()
    with pytest.raises(ToolError, match=r"Move did not finish within 0.5s; rail stopped at 42.0."):
        asyncio.run(server.rail_move_to(position=5, timeout_s=0.5))
    assert time.monotonic() - t0 >= 0.5
    assert rail.calls[-1] == ("stop",)


def test_status_and_realtime(use_rail):
    rail = use_rail(estop=True)
    status = server.rail_get_status()
    assert status == {
        "estop_pressed": True,
        "homing_in_progress": False,
        "rail": {
            "ready": True,
            "busy": False,
            "position": 812.5,
            "velocity": 100.0,
            "limit_error": False,
            "driver_error": False,
        },
        "turntable": {"position_deg": 725818.8, "ready": True, "busy": False, "driver_error": False},
    }
    with pytest.raises(ToolError, match=r"Rail is not connected; call rail_connect \(or device_check\) first."):
        server.get_realtime()
    rail.connected, rail.ip = True, "192.168.1.1"
    realtime = server.get_realtime()
    assert realtime["connected"] and realtime["ip"] == "192.168.1.1"
    assert realtime["rail_position_mm"] == 812.5 and realtime["rotation_angle_0_360"] == 58.8
    assert realtime["read_errors"] == {}


def test_status_reports_per_field_errors(use_rail):
    rail = use_rail()

    def broken(axis=0):
        raise RuntimeError("read failed")

    rail.position = broken
    status = server.rail_get_status()
    assert status["rail"]["position"] == "error: read failed"
    assert status["turntable"]["position_deg"] == "error: read failed"
    assert status["rail"]["ready"] is True
    assert server.rail_stop() == {"ok": True, "position": "error: read failed"}


def test_ranges_connect_and_turntable(use_rail):
    rail = use_rail()
    assert server.rail_get_ranges() == {
        "rail_position": (0.0, 1750.0),
        "rail_velocity": (1.0, 3000.0),
        "turntable_rotate_speed": (0.0, 360.0),
        "turntable_swing_speed": (0.1, 10.0),
    }
    assert asyncio.run(server.rail_connect()) == {"ok": True, "ip": "192.168.1.1"}
    assert server.turntable_rotate("backward", speed=30) == {"ok": True, "direction": "backward", "speed": 30}
    assert server.turntable_rotate("forward") == {"ok": True, "direction": "forward", "speed": None}
    assert server.turntable_swing(speed=2) == {"ok": True, "speed": 2}
    assert server.turntable_stop() == {"ok": True}
    assert server.rail_reset_alarms(clear_history=False) == {"ok": True}
    assert server.rail_reset_alarms() == {"ok": True}
    assert server.rail_disconnect() == {"ok": True}
    assert rail.calls == [
        ("connect", "192.168.1.1"),
        ("set_rotate_speed", 30),
        ("rotate", False),
        ("rotate", True),
        ("set_swing_speed", 2),
        ("swing",),
        ("stop_rotate",),
        ("reset_estop",),
        ("reset_estop",),
        ("clear_alarm_history",),
        ("disconnect",),
    ]
    with pytest.raises(ToolError, match=r"speed 400 is outside the allowed range \[0.0, 360.0\]"):
        server.turntable_rotate("forward", speed=400)
    with pytest.raises(ToolError, match=r"speed 0.05 is outside the allowed range \[0.1, 10.0\]"):
        server.turntable_swing(speed=0.05)


def test_connect_failure(use_rail):
    use_rail(connect_code=1)
    with pytest.raises(ToolError, match="Rail connection to 10.0.0.9 failed \\(code 1\\): message for code 1"):
        asyncio.run(server.rail_connect(ip="10.0.0.9"))


def test_home_without_wait(use_rail):
    rail = use_rail()
    assert asyncio.run(server.rail_home(axis="rotate", wait=False)) == {"ok": True, "started": True}
    assert rail.calls == [("home", 1)]
