"""Device self-check: connect what can be connected and show what can be read.

Runs at start-up (green/red summary on the console) and as the device_check tool.

- Rail PLC: connect, then read the same real-time values SW_MLB shows (rail
  position, rotation position) plus ready/busy, e-stop, limit/driver errors.
  A successful register read is the proof that the PLC is really there.
- Lights: open each configured COM port. The lights never answer, so this
  proves the port works, not that a lamp is listening behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .backend import LightBackend
from .ccxl import CcxlLightBank
from .config import LIGHTS, LhmConfig
from .errors import DeviceError
from .lights import LightBank, PortInfo, list_serial_ports
from .logs import check_log
from .rail import AXIS_RAIL, AXIS_ROTATE, RailController

PLC_IP = "192.168.1.1"


def read(fn: Callable, *args) -> tuple[Any, str | None]:
    """(value, None), or (None, error text): each reading reports its own failure."""
    try:
        return fn(*args), None
    except Exception as exc:  # noqa: BLE001 - any failure is shown next to the value
        return None, str(exc)


def realtime(rail: RailController) -> dict:
    """The values SW_MLB's 'real-time information' panel shows, plus axis state."""
    values, errors = {}, {}
    for key, fn, args in (
        ("rail_position_mm", rail.position, (AXIS_RAIL,)),
        ("rotation_position_deg", rail.position, (AXIS_ROTATE,)),
        ("rail_velocity", rail.velocity, (AXIS_RAIL,)),
        ("rail_ready", rail.ready, (AXIS_RAIL,)),
        ("rail_busy", rail.busy, (AXIS_RAIL,)),
        ("rotation_ready", rail.ready, (AXIS_ROTATE,)),
        ("rotation_busy", rail.busy, (AXIS_ROTATE,)),
        ("estop_pressed", rail.estop_pressed, ()),
        ("homing_in_progress", rail.homing, ()),
        ("rail_limit_error", rail.limit_error, (AXIS_RAIL,)),
        ("rail_driver_error", rail.driver_error, (AXIS_RAIL,)),
        ("rotation_driver_error", rail.driver_error, (AXIS_ROTATE,)),
    ):
        values[key], err = read(fn, *args)
        if err:
            errors[key] = err
    raw = values.get("rotation_position_deg")
    # The PLC reports the accumulated angle (e.g. 725818.8 = 2016 turns + 58.8 deg).
    values["rotation_angle_0_360"] = round(raw % 360, 3) if isinstance(raw, float) else None
    return {"connected": rail.connected, "ip": rail.ip, **values, "read_errors": errors}


def _line(status: str, text: str) -> None:
    tag = {"ok": "[ OK ]", "fail": "[FAIL]", "warn": "[SKIP]", "info": "      "}[status]
    log = check_log.warning if status == "fail" else check_log.info
    log("%s %s", tag, text, extra={"status": status})


def _fmt(value, unit: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}{unit}"
    return f"{value}{unit}"


def run(rail: RailController, lights: LightBackend, cfg: LhmConfig, ip: str = PLC_IP) -> dict:
    check_log.info("=" * 22 + " device check " + "=" * 22, extra={"status": "info"})

    # --- rail / turntable PLC -------------------------------------------------
    rail_ok, rail_error = False, None
    try:
        code = rail.connect(ip) if not rail.connected else 0
        if code != 0:
            rail_error = f"code {code}: {rail.message(code)}"
    except DeviceError as exc:
        rail_error = str(exc)
    snapshot = realtime(rail) if rail.connected else None
    if rail.connected and snapshot and snapshot["rail_position_mm"] is not None:
        rail_ok = True
        _line("ok", f"Rail/turntable PLC {ip}: connected, registers readable")
        _line("info", f"  Rail position      : {_fmt(snapshot['rail_position_mm'], ' mm')}")
        _line(
            "info",
            f"  Rotation position  : {_fmt(snapshot['rotation_position_deg'], ' deg')} "
            f"(= {_fmt(snapshot['rotation_angle_0_360'], ' deg')} within one turn)",
        )
        _line("info", f"  Rail velocity      : {_fmt(snapshot['rail_velocity'])}")
        _line("info", f"  Rail ready / busy  : {_fmt(snapshot['rail_ready'])} / {_fmt(snapshot['rail_busy'])}")
        _line("info", f"  Rotation ready/busy: {_fmt(snapshot['rotation_ready'])} / {_fmt(snapshot['rotation_busy'])}")
        estop = snapshot["estop_pressed"]
        _line("fail" if estop else "info", f"  E-stop pressed     : {_fmt(estop)}")
        for key in ("rail_limit_error", "rail_driver_error", "rotation_driver_error"):
            if snapshot[key]:
                _line("fail", f"  {key.replace('_', ' ')}: yes")
        for key, err in snapshot["read_errors"].items():
            _line("warn", f"  {key}: {err}")
    else:
        rail_error = rail_error or (snapshot and next(iter(snapshot["read_errors"].values()), None)) or "no data"
        _line("fail", f"Rail/turntable PLC {ip}: NOT connected ({rail_error})")
        _line("info", "  Check the USB cable / power and that the USB-Ethernet adapter is set to 192.168.1.x")

    # --- lights ------------------------------------------------------------------
    present = {p.device.upper(): p for p in list_serial_ports()}
    light_results = {}
    if isinstance(lights, CcxlLightBank):
        _check_ccxl(lights, light_results)
    else:
        _check_serial_lights(lights, cfg, present, light_results)
    if present:
        ports = ", ".join(
            f"{d} ({p.vid or 0:04X}:{p.pid or 0:04X}{' LHM' if p.likely_lhm_light else ''})" for d, p in present.items()
        )
        _line("info", f"  Serial ports on this PC: {ports}")
    else:
        _line("info", "  No serial ports on this PC")

    lights_ok = any(v in ("port open", "controller answered") for v in light_results.values())
    if rail_ok and lights_ok:
        _line("ok", "STATUS: CONNECTED (rail PLC + lights)")
    elif rail_ok:
        _line("ok", "STATUS: RAIL CONNECTED  (lights not connected)")
    elif lights_ok:
        _line("fail", "STATUS: LIGHTS CONNECTED, RAIL NOT CONNECTED")
    else:
        _line("fail", "STATUS: NOT CONNECTED")
    check_log.info("=" * 58, extra={"status": "info"})
    return {
        "rail_ok": rail_ok,
        "rail_error": None if rail_ok else rail_error,
        "realtime": snapshot,
        "lights": light_results,
        "light_backend": lights.backend,
    }


def _check_ccxl(lights: CcxlLightBank, light_results: dict) -> None:
    """CCXL controllers answer a handshake, so a connect proves the controller is there."""
    try:
        for name in LIGHTS:
            lights.get(name).open(None)
        for name in LIGHTS:
            light_results[name] = "controller answered"
        _line(
            "ok",
            f"Light controller: answered on {lights.ctl.port} via CCXL_calibrate.dll "
            f"(top/up/down = address {'/'.join(str(lights.get(n).address) for n in LIGHTS)})",
        )
    except DeviceError as exc:
        for name in LIGHTS:
            light_results[name] = str(exc)
        _line("fail", f"Light controller: {exc}")


def _check_serial_lights(lights: LightBank, cfg: LhmConfig, present: dict[str, PortInfo], light_results: dict) -> None:
    for name in LIGHTS:
        light = lights.get(name)
        port = light.port or cfg.lights[name].port
        if not port:
            light_results[name] = "not configured"
            _line("warn", f"{name:>4} light: no COM port configured (LHMconfig.ini [Connect])")
            continue
        if port.upper() not in present and not port.startswith(("loop://", "socket://")):
            light_results[name] = f"{port} not present"
            _line("fail", f"{name:>4} light: {port} does not exist on this PC")
            continue
        try:
            if not light.connected:
                light.open(port)
            light_results[name] = "port open"
            _line(
                "ok",
                f"{name:>4} light: {port} open (9600 8N1, address {light.address}); "
                "lights never reply, so the lamp itself cannot be confirmed",
            )
        except DeviceError as exc:
            light_results[name] = str(exc)
            _line("fail", f"{name:>4} light: {exc}")
