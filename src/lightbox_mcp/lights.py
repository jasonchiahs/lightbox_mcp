"""The three MLB-LHM lights (top, up, down), each on its own USB serial port.

Talks the vendor protocol directly (see protocol.py) instead of loading
LHM_TopLight.dll / LHM_FlikerLight.dll, so every byte on the wire is logged
and a bad argument can never reach the DLLs' exit() on invalid baud rates.
The command sequence and timing mirror SW_MLB's LHM_V2_Product::SetLightParam.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import ClassVar

import serial
from serial.tools import list_ports

from . import protocol
from .backend import timestamp
from .config import LIGHTS, LhmConfig, normalize_port
from .errors import DeviceError, light_not_connected
from .logs import light_log

# USB IDs of the lights' serial adapters, from LHMconfig.ini [Connect].
LHM_VID, LHM_PID = 0x104C, 0x82CD
# SW_MLB waits 300 ms between the parameter, second-parameter and mode commands.
COMMAND_GAP_S = 0.3
# The DLLs purge, wait 10 ms, then write.
PRE_WRITE_DELAY_S = 0.01
# The lights do not reply; anything they send anyway is logged after this window.
RX_WINDOW_S = 0.05
OFF_CCT = 2300  # SW_MLB turns a light off with illuminance 0 at 2300 K


@dataclass
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    location: str | None
    likely_lhm_light: bool


def list_serial_ports() -> list[PortInfo]:
    return [
        PortInfo(
            device=p.device,
            description=p.description,
            vid=p.vid,
            pid=p.pid,
            serial_number=p.serial_number,
            location=p.location,
            likely_lhm_light=(p.vid, p.pid) == (LHM_VID, LHM_PID),
        )
        for p in sorted(list_ports.comports(), key=lambda p: p.device)
    ]


class SharedPort:
    """One open serial port, shared by every light on it.

    On the MLB-LHM controller all three lights sit behind one USB serial port
    and are told apart by the address byte of each frame, so the port is
    opened once and the lock keeps frames from different lights apart.
    """

    _registry: ClassVar[dict[str, SharedPort]] = {}
    _registry_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, name: str, ser: serial.Serial) -> None:
        self.name = name
        self.serial = ser
        self.users: set[str] = set()
        self.lock = threading.Lock()

    @classmethod
    def acquire(cls, port: str, user: str) -> SharedPort:
        key = normalize_port(port)
        with cls._registry_lock:
            shared = cls._registry.get(key)
            if shared is None or not shared.serial.is_open:
                light_log.info("open %s (%d 8N1)", key, protocol.BAUD_RATE)
                try:
                    # serial_for_url also accepts pyserial URLs such as loop:// (used by the tests).
                    ser = serial.serial_for_url(
                        key,
                        protocol.BAUD_RATE,
                        bytesize=8,
                        parity="N",
                        stopbits=1,
                        timeout=0,
                        write_timeout=1,
                    )
                except (serial.SerialException, ValueError) as exc:
                    light_log.warning("open %s failed: %s", key, exc)
                    raise DeviceError(f"Cannot open {key} for the {user} light: {exc}") from exc
                shared = cls._registry[key] = SharedPort(key, ser)
            shared.users.add(user)
            return shared

    def release(self, user: str) -> None:
        with self._registry_lock:
            self.users.discard(user)
            if not self.users:
                light_log.info("close %s", self.name)
                try:
                    self.serial.close()
                finally:
                    self._registry.pop(self.name, None)


class Light:
    def __init__(self, name: str, address: int) -> None:
        self.name = name
        self.address = address
        self._port: SharedPort | None = None
        # What was last sent: the lights cannot be queried, so this is the only state there is.
        self.last_command: dict | None = None

    @property
    def port(self) -> str | None:
        return self._port.name if self._port else None

    @property
    def connected(self) -> bool:
        return self._port is not None and self._port.serial.is_open

    def open(self, port: str) -> None:
        self.close()
        self._port = SharedPort.acquire(port, self.name)
        light_log.info("%s light -> %s (address %d)", self.name, self._port.name, self.address)

    def close(self) -> None:
        if self._port is not None:
            port, self._port = self._port, None
            port.release(self.name)

    def send(self, frame: bytes) -> None:
        if not self.connected:
            raise light_not_connected(self.name)
        shared = self._port
        with shared.lock:
            s = shared.serial
            s.reset_input_buffer()
            s.reset_output_buffer()
            time.sleep(PRE_WRITE_DELAY_S)
            light_log.info(
                "%s %s TX %s  (%s)", self.name, shared.name, protocol.to_hex(frame), protocol.describe(frame)
            )
            try:
                s.write(frame)
                s.flush()
            except serial.SerialException as exc:
                light_log.warning("%s %s TX failed: %s", self.name, shared.name, exc)
                raise DeviceError(f"Write to {shared.name} ({self.name} light) failed: {exc}") from exc
            time.sleep(RX_WINDOW_S)
            pending = s.in_waiting
            if pending:
                light_log.info("%s %s RX %s", self.name, shared.name, protocol.to_hex(s.read(pending)))


class LightBank:
    backend = "LHM serial protocol (DeviceVersion V2)"

    def __init__(self, cfg: LhmConfig) -> None:
        self.cfg = cfg
        self.lights = {n: Light(n, cfg.lights[n].address) for n in LIGHTS}

    def get(self, name: str) -> Light:
        return self.lights[name]

    def connect_lights(self, port: str | None, ports: dict[str, str | None]) -> dict:
        """Open each light's own port, else the shared `port`, else its LHMconfig.ini [Connect] port.

        Lights that fail are reported in "errors"; it is an error only if none could be opened.
        """
        wanted = {name: ports.get(name) or port or self.cfg.lights[name].port for name in LIGHTS}
        plan = {name: normalize_port(target) for name, target in wanted.items() if target}
        if not plan:
            raise DeviceError("No COM ports given and none configured in LHMconfig.ini; see light_list_ports.")
        errors = {}
        for name, target in plan.items():
            try:
                self.get(name).open(target)
            except DeviceError as exc:
                errors[name] = str(exc)
        if len(errors) == len(plan):
            raise DeviceError(f"No light could be connected: {errors}")
        return {"lights": self.status(), "errors": errors}

    def status(self) -> dict:
        return {
            name: {
                "connected": light.connected,
                "port": light.port,
                "address": light.address,
                "configured_port": self.cfg.lights[name].port,
                "last_command": light.last_command,
            }
            for name, light in self.lights.items()
        }

    def close_all(self) -> None:
        for light in self.lights.values():
            light.close()

    def supports(self, light: str, mode: int) -> bool:
        # Top: white/infrared. Up/down: white/flicker; LHM_FlikerLight.dll has no infrared command.
        if light == "top":
            return mode in (protocol.MODE_WHITE, protocol.MODE_INFRARED)
        return mode in (protocol.MODE_WHITE, protocol.MODE_FLICKER)

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
        """SW_MLB's SetLightParam: illuminance/CCT, then IR (top) or frequency/duty
        (up/down), then the mode that selects which of them the light uses."""
        light = self.get(name)
        addr, has_cct = light.address, self.cfg.has_cct
        params = {"illuminance": illuminance, "cct": cct}
        if name == "top":
            frames = [
                protocol.top_lux_kelvin(addr, illuminance, cct),
                protocol.top_infrared(addr, ir_850, ir_940),
                protocol.set_mode(addr, mode),
            ]
            params.update(ir_850=ir_850, ir_940=ir_940)
        else:
            frames = [
                protocol.flicker_lux_kelvin(addr, illuminance, cct, has_cct),
                protocol.flicker_freq_duty(addr, frequency, duty, has_cct),
                protocol.set_mode(addr, mode),
            ]
            params.update(frequency=frequency, duty=duty)
        self._send_all(light, frames)
        light.last_command = {"mode": mode, **params, "at": timestamp()}
        return [protocol.to_hex(f) for f in frames]

    def off(self, name: str) -> list[str]:
        light = self.get(name)
        if name == "top":
            frames = [protocol.top_lux_kelvin(light.address, 0, OFF_CCT), protocol.top_infrared(light.address, 0, 0)]
        else:
            frames = [protocol.flicker_lux_kelvin(light.address, 0, OFF_CCT, self.cfg.has_cct)]
        self._send_all(light, frames)
        light.last_command = {"off": True, "at": timestamp()}
        return [protocol.to_hex(f) for f in frames]

    @staticmethod
    def _send_all(light: Light, frames: list[bytes]) -> None:
        for i, frame in enumerate(frames):
            if i:
                time.sleep(COMMAND_GAP_S)
            light.send(frame)
