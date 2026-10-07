"""rail_home must wait for the axis to stop moving: the PLC never sets its home-done flag."""

import asyncio
import time

import pytest

from lightbox_mcp import server


class FakeRail:
    def __init__(self, busy_samples):
        self.busy_samples = list(busy_samples)
        self.homed = None

    def home(self, axis):
        self.homed = axis

    def busy(self, axis):
        return self.busy_samples.pop(0) if self.busy_samples else False

    def homing(self):
        return False  # as observed on the lightbox

    def position(self, axis=0):
        return 0.0

    def polling(self):
        import contextlib
        return contextlib.nullcontext()


def run(fake, monkeypatch, **kwargs):
    monkeypatch.setattr(server, "rail", fake)
    return asyncio.run(server.rail_home(**kwargs))


def test_waits_until_motion_ends(monkeypatch):
    # first sample idle (motion not started yet), then 4 busy samples, then idle
    fake = FakeRail([False, True, True, True, True, False, False])
    t0 = time.monotonic()
    result = run(fake, monkeypatch, axis="rotate", wait=True, timeout_s=30)
    assert fake.homed == 1 and result["ok"]
    assert time.monotonic() - t0 >= 0.3 * 6  # did not return while busy


def test_axis_already_home_returns_after_grace(monkeypatch):
    fake = FakeRail([])  # never busy
    t0 = time.monotonic()
    run(fake, monkeypatch, axis="rail", wait=True, timeout_s=30)
    assert time.monotonic() - t0 >= server.HOME_START_GRACE_S


def test_timeout(monkeypatch):
    fake = FakeRail([True] * 100)
    with pytest.raises(Exception, match="did not finish"):
        run(fake, monkeypatch, axis="rail", wait=True, timeout_s=1)
