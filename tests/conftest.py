"""Shared test doubles."""

import contextlib

import pytest


class FakeRail:
    """Stand-in for RailController: scripted readings, every command recorded in `calls`."""

    def __init__(self, busy=(), connect_code=0, **readings):
        self.connected = False
        self.ip = None
        self.calls = []
        self.busy_samples = list(busy)
        self.connect_code = connect_code
        self.readings = {
            "position": 812.5,
            "rotation": 725818.8,
            "velocity": 100.0,
            "ready": True,
            "estop": False,
            "homing": False,
            "limit_error": False,
            "driver_error": False,
            **readings,
        }

    def polling(self):
        return contextlib.nullcontext()

    # --- connection ---------------------------------------------------------
    def connect(self, ip):
        self.calls.append(("connect", ip))
        self.connected = self.connect_code == 0
        self.ip = ip if self.connected else None
        return self.connect_code

    def disconnect(self):
        self.calls.append(("disconnect",))
        self.connected, self.ip = False, None

    def message(self, code):
        return f"message for code {code}"

    # --- readings -----------------------------------------------------------
    def ready(self, axis):
        return self.readings["ready"]

    def busy(self, axis):
        return self.busy_samples.pop(0) if self.busy_samples else False

    def position(self, axis=0):
        return self.readings["position"] if axis == 0 else self.readings["rotation"]

    def velocity(self, axis=0):
        return self.readings["velocity"]

    def limit_error(self, axis=0):
        return self.readings["limit_error"]

    def driver_error(self, axis):
        return self.readings["driver_error"]

    def estop_pressed(self):
        return self.readings["estop"]

    def homing(self):
        return self.readings["homing"]

    def position_range(self, axis=0):
        return 0.0, 1750.0

    def velocity_range(self, axis=0):
        return 1.0, 3000.0

    def rotate_speed_range(self):
        return 0.0, 360.0

    def swing_speed_range(self):
        return 0.1, 10.0

    # --- commands -----------------------------------------------------------
    def __getattr__(self, name):
        if name in {
            "set_velocity",
            "start_move_to",
            "stop",
            "home",
            "reset_estop",
            "clear_alarm_history",
            "set_rotate_speed",
            "set_swing_speed",
            "rotate",
            "swing",
            "stop_rotate",
        }:
            return lambda *args: self.calls.append((name, *args))
        raise AttributeError(name)


@pytest.fixture
def fake_rail():
    return FakeRail
