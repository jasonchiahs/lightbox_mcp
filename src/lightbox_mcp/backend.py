"""The interface both light backends implement, and what they share.

SW_MLB picks the light implementation from LHMconfig.ini [DeviceInfo] DeviceVersion:
"V2" -> the LHM serial protocol (lights.LightBank); "V1", "V1.1", "V1.2" ->
CCXL_calibrate.dll (ccxl.CcxlLightBank). The MCP tools and the self-check only
use the methods below, so they work with either.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .config import LhmConfig


class LightHandle(Protocol):
    """One of the top/up/down lights."""

    name: str
    address: int
    # What was last sent: the lights cannot be queried, so this is the only state there is.
    last_command: dict | None

    @property
    def connected(self) -> bool: ...

    @property
    def port(self) -> str | None: ...

    def open(self, port: str | None) -> None: ...

    def close(self) -> None: ...


class LightBackend(Protocol):
    """The three lights, driven one way or the other."""

    backend: str
    cfg: LhmConfig

    def get(self, name: str) -> LightHandle: ...

    def connect_lights(self, port: str | None, ports: dict[str, str | None]) -> dict:
        """Connect the lights: `port` for all of them, or `ports` per light name."""

    def status(self) -> dict: ...

    def close_all(self) -> None: ...

    def supports(self, light: str, mode: int) -> bool: ...

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
        """Send SW_MLB's SetLightParam; returns what was sent, for the reply."""

    def off(self, name: str) -> list[str]: ...


def timestamp() -> str:
    """When a command was sent, for last_command: the lightbox PC's local time."""
    return datetime.now().isoformat(timespec="seconds")  # noqa: DTZ005 - local wall-clock time on purpose
