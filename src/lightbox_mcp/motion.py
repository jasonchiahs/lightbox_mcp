"""Waiting for rail and turntable motion to finish.

The PLC reports motion only through each axis's busy signal, so completion is
judged by polling it (status reads are logged at DEBUG while polling).
"""

from __future__ import annotations

import asyncio
import time

from .errors import DeviceError
from .rail import AXES, AXIS_RAIL, RailController

# Time the PLC needs to raise busy after a start command.
START_DELAY_S = 0.3
MOVE_POLL_S = 0.2
HOME_POLL_S = 0.3


async def wait_for_move(rail: RailController, timeout_s: float) -> None:
    """Wait until the rail stops; stop it and raise on a limit/driver fault or a timeout."""
    deadline = time.monotonic() + timeout_s
    await asyncio.sleep(START_DELAY_S)
    while True:
        with rail.polling():
            busy = rail.busy(AXIS_RAIL)
            fault = busy and (rail.limit_error(AXIS_RAIL) or rail.driver_error(AXIS_RAIL))
        if not busy:
            return
        if fault:
            rail.stop()
            raise DeviceError("Rail reported a limit/driver error during the move; stopped.")
        if time.monotonic() > deadline:
            rail.stop()
            raise DeviceError(f"Move did not finish within {timeout_s}s; rail stopped at {rail.position()}.")
        await asyncio.sleep(MOVE_POLL_S)


async def wait_for_home(rail: RailController, axis: str, timeout_s: float, grace_s: float) -> float:
    """Wait until homing of `axis` ("rail" or "rotate") is over; returns the seconds it took.

    The PLC's home-done flag (GetHomeDone) was never seen set on the lightbox, so completion
    is judged by the axis busy signal: wait for motion to start (or `grace_s` to pass, for an
    axis already at its origin), then for two consecutive idle samples with no homing flag.
    """
    index = AXES[axis]
    start = time.monotonic()
    deadline = start + timeout_s
    seen_busy, idle_samples = False, 0
    await asyncio.sleep(START_DELAY_S)
    while True:
        with rail.polling():
            busy = rail.busy(index)
            homing = rail.homing()
        seen_busy = seen_busy or busy
        idle = not busy and not homing
        idle_samples = idle_samples + 1 if idle else 0
        if idle_samples >= 2 and (seen_busy or time.monotonic() - start > grace_s):
            return round(time.monotonic() - start, 1)
        if time.monotonic() > deadline:
            raise DeviceError(f"Homing of the {axis} axis did not finish within {timeout_s}s.")
        await asyncio.sleep(HOME_POLL_S)
