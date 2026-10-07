"""MCP server for the YANDING MLB-LHM V2.0 lightbox (SW_MLB V2.7 device set).

- Top light (surface light): white (CCT + illuminance) or infrared (850/940 nm)
- Up / down lights (bar lights): white or flicker (frequency + duty)
- Rail and turntable behind the PLC at 192.168.1.1 (LHMGuideV2.dll)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import cli, config, logs, motion, protocol, selfcheck
from .backend import LightBackend
from .ccxl import CcxlLightBank
from .config import EMBEDDED_CONFIG, LIGHTS, LhmConfig
from .errors import light_not_connected
from .lights import LightBank, list_serial_ports
from .middleware import TrafficLogger
from .rail import AXES, AXIS_RAIL, AXIS_ROTATE, RailController

INSTRUCTIONS = """\
Controls a YANDING MLB-LHM V2.0 lightbox (the device set of the vendor's SW_MLB app).

Hardware:
- top light: surface light. Mode 1 white (CCT 2300-10000 K, illuminance 0-60000 steps),
  mode 2 infrared (850 nm / 940 nm levels 0-60000).
- up and down lights: bar lights. Mode 1 white, mode 2 infrared, mode 3 flicker
  (0-1000 Hz, duty 1-99 %). light_status shows which light backend is active.
- rail (0-1750 mm, 1-3000 mm/s) and turntable (rotate 0-360 RPM, swing 0.1-10)
  behind a PLC at 192.168.1.1, reached over the controller's USB-Ethernet adapter.

Workflow:
- At start-up the server already tries to connect the rail PLC and the configured light
  ports. device_check repeats that and reports what is connected; get_realtime returns
  the rail position (mm) and rotation position (deg).
- Lights: light_connect (ports from LHMconfig.ini, or pass top_port/up_port/down_port;
  light_list_ports shows candidates). Then light_set_white / light_set_infrared /
  light_set_flicker; light_off turns lights off. The lights never reply, so a
  successful call means the command was written to the port.
- Rail/turntable: rail_connect, rail_get_status (ready=true, estop_pressed=false)
  before moving; rail_move_to; turntable_rotate / turntable_swing run until turntable_stop.
"""

# SDK/uvicorn at WARNING: TrafficLogger already records every request and reply,
# so their per-request INFO lines (HTTP access log, session ids) are just noise.
mcp = MCPServer("mlb-lhm", instructions=INSTRUCTIONS, middleware=[TrafficLogger()], log_level="WARNING")


def make_light_bank(c: LhmConfig) -> LightBackend:
    """SW_MLB's rule: DeviceVersion V2 -> LHM serial protocol, V1.x -> CCXL_calibrate.dll."""
    return LightBank(c) if c.uses_lhm_serial else CcxlLightBank(c)


# Device state, shared by all tools. main() reloads cfg/lights from the chosen LHMconfig.ini
# and sets plc_ip from --plc-ip.
cfg = config.load(None)
lights = make_light_bank(cfg)
rail = RailController()
plc_ip = selfcheck.PLC_IP

LightName = Literal["top", "up", "down"]
FlickerLightName = Literal["up", "down"]
Level = Annotated[int, Field(ge=0, le=65535)]

# An axis already at its origin may never report busy; after this long, idle counts as done.
HOME_START_GRACE_S = 1.5


def _check(light: str, param: str, value: float) -> None:
    lo, hi = cfg.range(light, param)
    if not lo <= value <= hi:
        raise ToolError(f"{param} {value} is outside the {light} light's range [{lo}, {hi}] (LHMconfig.ini)")


def _require_connected(light: str) -> None:
    if not lights.get(light).connected:
        raise light_not_connected(light)


def _in_range(name: str, value: float, bounds: tuple[float, float]) -> None:
    lo, hi = bounds
    if lo < hi and not lo <= value <= hi:
        raise ToolError(f"{name} {value} is outside the allowed range [{lo}, {hi}]")


async def _run(fn, *args):
    """Light commands sleep between frames (like SW_MLB); keep the event loop free."""
    return await anyio.to_thread.run_sync(fn, *args)


def _safe(fn, *args):
    """A reading, or its error text: one failing field must not fail the whole status."""
    value, error = selfcheck.read(fn, *args)
    return value if error is None else f"error: {error}"


# =============================================================================
# Lights: top (surface light) and up/down (bar lights with flicker)
# =============================================================================


@mcp.tool()
def light_list_ports() -> dict:
    """List the PC's serial ports. The lights' USB serial adapters are flagged likely_lhm_light (VID 104C, PID 82CD)."""
    return {"ports": [vars(p) for p in list_serial_ports()], "configured": lights.status()}


@mcp.tool()
async def light_connect(
    port: Annotated[
        str | None,
        Field(
            description="One COM port for all three lights (the MLB-LHM controller "
            "puts them on one bus, told apart by address), e.g. COM4"
        ),
    ] = None,
    top_port: Annotated[str | None, Field(description="COM port of the top light, e.g. COM24")] = None,
    up_port: Annotated[str | None, Field(description="COM port of the up light, e.g. COM26")] = None,
    down_port: Annotated[str | None, Field(description="COM port of the down light, e.g. COM25")] = None,
) -> dict:
    """Connect the lights. CCXL controllers (DeviceVersion V1.x): one connection for all
    lights; without a port every COM port is scanned for a controller that answers, as SW_MLB
    does. LHM V2 controllers: per-light serial ports, defaults from LHMconfig.ini [Connect]."""
    ports = {"top": top_port, "up": up_port, "down": down_port}
    return await _run(lights.connect_lights, port, ports)


@mcp.tool()
def light_disconnect() -> dict:
    """Close all light serial ports."""
    lights.close_all()
    return {"ok": True}


@mcp.tool()
def light_status() -> dict:
    """Connection state, addresses and parameter ranges of the three lights."""
    return {
        "lights": lights.status(),
        "backend": lights.backend,
        "device_version": cfg.device_version,
        "flicker_has_cct": cfg.has_cct,
        "ranges": cfg.ranges,
        "config_file": str(cfg.source) if cfg.source else None,
        "config_embedded": cfg.source == EMBEDDED_CONFIG,
        "config_warnings": cfg.warnings,
    }


@mcp.tool()
async def light_set_white(
    light: LightName,
    cct: Annotated[int, Field(description="Colour temperature in K (default range 2300-10000)")],
    illuminance: Annotated[int, Field(description="Illuminance in steps (default range 0-60000)")],
) -> dict:
    """Mode 1 (white): set colour temperature and illuminance of the top, up or down light."""
    _require_connected(light)
    _check(light, "cct", cct)
    _check(light, "illuminance", illuminance)
    frames = await _run(lights.set_param, light, protocol.MODE_WHITE, illuminance, cct)
    return {
        "ok": True,
        "light": light,
        "mode": protocol.MODE_WHITE,
        "cct": cct,
        "illuminance": illuminance,
        "frames": frames,
    }


@mcp.tool()
async def light_set_infrared(
    ir_850: Annotated[int, Field(description="850 nm level in steps (default range 0-60000)")],
    ir_940: Annotated[int, Field(description="940 nm level in steps (default range 0-60000)")],
    light: Annotated[LightName, Field(description="Up/down only with the CCXL backend")] = "top",
) -> dict:
    """Mode 2 (infrared): set the 850 nm and 940 nm levels."""
    if not lights.supports(light, protocol.MODE_INFRARED):
        raise ToolError(f"The {light} light has no infrared mode with the {lights.backend} backend.")
    _require_connected(light)
    _check(light, "ir_850", ir_850)
    _check(light, "ir_940", ir_940)
    lowest_cct = cfg.range(light, "cct")[0]
    frames = await _run(lights.set_param, light, protocol.MODE_INFRARED, 0, lowest_cct, ir_850, ir_940)
    return {
        "ok": True,
        "light": light,
        "mode": protocol.MODE_INFRARED,
        "ir_850": ir_850,
        "ir_940": ir_940,
        "frames": frames,
    }


@mcp.tool()
async def light_set_flicker(
    light: FlickerLightName,
    frequency: Annotated[int, Field(description="Flicker frequency in Hz (default range 0-1000)")],
    duty: Annotated[int, Field(description="Duty cycle in % (default range 1-99)")],
    illuminance: Annotated[int, Field(description="Flicker illuminance in steps (default range 0-60000)")],
    cct: Annotated[int, Field(description="Colour temperature in K sent with the illuminance")] = 5000,
) -> dict:
    """Mode 3 (flicker) of the up or down light: frequency, duty cycle and illuminance."""
    _require_connected(light)
    for param, value in (("frequency", frequency), ("duty", duty), ("illuminance", illuminance), ("cct", cct)):
        _check(light, param, value)
    frames = await _run(lights.set_param, light, protocol.MODE_FLICKER, illuminance, cct, 0, 0, frequency, duty)
    result = {
        "ok": True,
        "light": light,
        "mode": protocol.MODE_FLICKER,
        "frequency": frequency,
        "duty": duty,
        "illuminance": illuminance,
        "frames": frames,
    }
    if cfg.uses_lhm_serial and cfg.has_cct:
        result["note"] = (
            "With [FlickerLightParamFlag] CCT=1 the vendor protocol does not transmit the duty cycle (same as SW_MLB)."
        )
    return result


@mcp.tool()
async def light_set_raw(
    light: LightName,
    mode: Annotated[Literal[1, 2, 3], Field(description="1 white, 2 infrared, 3 flicker (3 is up/down only)")],
    illuminance: Level = 0,
    cct: Level = 2300,
    ir_850: Annotated[int, Field(ge=0, le=65535, description="Top light only")] = 0,
    ir_940: Annotated[int, Field(ge=0, le=65535, description="Top light only")] = 0,
    frequency: Annotated[int, Field(ge=0, le=65535, description="Up/down light only")] = 0,
    duty: Annotated[int, Field(ge=0, le=255, description="Up/down light only")] = 1,
) -> dict:
    """Send exactly what SW_MLB's SetLightParam sends: illuminance/CCT, then IR (top) or
    frequency/duty (up/down), then the mode. For cases the other light tools don't cover."""
    _require_connected(light)
    if not lights.supports(light, mode):
        raise ToolError(f"The {light} light has no mode {mode} with the {lights.backend} backend.")
    frames = await _run(lights.set_param, light, mode, illuminance, cct, ir_850, ir_940, frequency, duty)
    return {"ok": True, "light": light, "mode": mode, "frames": frames}


@mcp.tool()
async def light_off(light: Literal["top", "up", "down", "all"] = "all") -> dict:
    """Turn a light off (illuminance 0, as SW_MLB does). 'all' covers every connected light."""
    if light == "all":
        targets = [name for name in LIGHTS if lights.get(name).connected]
        if not targets:
            raise ToolError("No light is connected; call light_connect first.")
    else:
        _require_connected(light)
        targets = [light]
    sent = {name: await _run(lights.off, name) for name in targets}
    return {"ok": True, "off": targets, "frames": sent}


# =============================================================================
# Rail and turntable
# =============================================================================


@mcp.tool()
async def rail_connect(
    ip: Annotated[str, Field(description="PLC address; SW_MLB always uses 192.168.1.1")] = "192.168.1.1",
) -> dict:
    """Connect to the rail/turntable PLC (the PC's USB-Ethernet adapter must be on 192.168.1.x)."""
    code = await anyio.to_thread.run_sync(rail.connect, ip)
    if code != 0:
        raise ToolError(f"Rail connection to {ip} failed (code {code}): {rail.message(code)}")
    return {"ok": True, "ip": ip}


@mcp.tool()
def rail_disconnect() -> dict:
    """Disconnect from the rail/turntable PLC."""
    rail.disconnect()
    return {"ok": True}


@mcp.tool()
def rail_get_status() -> dict:
    """Read rail and turntable state: ready/busy, position, speed, errors, e-stop, homing."""
    return {
        "estop_pressed": _safe(rail.estop_pressed),
        "homing_in_progress": _safe(rail.homing),
        "rail": {
            "ready": _safe(rail.ready, AXIS_RAIL),
            "busy": _safe(rail.busy, AXIS_RAIL),
            "position": _safe(rail.position, AXIS_RAIL),
            "velocity": _safe(rail.velocity, AXIS_RAIL),
            "limit_error": _safe(rail.limit_error, AXIS_RAIL),
            "driver_error": _safe(rail.driver_error, AXIS_RAIL),
        },
        "turntable": {
            "position_deg": _safe(rail.position, AXIS_ROTATE),
            "ready": _safe(rail.ready, AXIS_ROTATE),
            "busy": _safe(rail.busy, AXIS_ROTATE),
            "driver_error": _safe(rail.driver_error, AXIS_ROTATE),
        },
    }


@mcp.tool()
def get_realtime() -> dict:
    """Real-time values, as SW_MLB's 'real-time information' panel: rail position (mm),
    rotation position (deg), plus ready/busy, e-stop and error flags. Needs rail_connect."""
    if not rail.connected:
        raise ToolError("Rail is not connected; call rail_connect (or device_check) first.")
    return selfcheck.realtime(rail)


@mcp.tool()
async def device_check() -> dict:
    """Connect the rail PLC and the configured light ports, and report what can be read.
    Same check as at start-up; the console shows a green/red summary."""
    return await anyio.to_thread.run_sync(selfcheck.run, rail, lights, cfg, plc_ip)


@mcp.tool()
def rail_get_ranges() -> dict:
    """Valid ranges for rail position/speed and turntable rotate/swing speed."""
    return {
        "rail_position": rail.position_range(AXIS_RAIL),
        "rail_velocity": rail.velocity_range(AXIS_RAIL),
        "turntable_rotate_speed": rail.rotate_speed_range(),
        "turntable_swing_speed": rail.swing_speed_range(),
    }


@mcp.tool()
async def rail_move_to(
    position: Annotated[float, Field(description="Target rail position (see rail_get_ranges)")],
    velocity: Annotated[float | None, Field(description="Move speed; omit to keep the current setting")] = None,
    wait: Annotated[bool, Field(description="Block until the move finishes")] = True,
    timeout_s: Annotated[float, Field(gt=0, le=600)] = 120,
) -> dict:
    """Move the rail to an absolute position. Stops the rail if the wait times out."""
    _in_range("position", position, rail.position_range(AXIS_RAIL))
    if velocity is not None:
        _in_range("velocity", velocity, rail.velocity_range(AXIS_RAIL))
    if rail.estop_pressed():
        raise ToolError("Emergency stop is pressed; release it and call rail_reset_alarms.")
    if not rail.ready(AXIS_RAIL):
        raise ToolError("Rail axis is not ready; check rail_get_status / rail_reset_alarms.")
    if velocity is not None:
        rail.set_velocity(velocity)
    rail.start_move_to(position)
    if not wait:
        return {"ok": True, "started": True, "target": position}
    await motion.wait_for_move(rail, timeout_s)
    return {"ok": True, "target": position, "position": rail.position()}


@mcp.tool()
def rail_stop() -> dict:
    """Stop rail motion immediately."""
    rail.stop()
    return {"ok": True, "position": _safe(rail.position)}


@mcp.tool()
async def rail_home(
    axis: Literal["rail", "rotate"] = "rail",
    wait: bool = True,
    timeout_s: Annotated[float, Field(gt=0, le=600)] = 180,
) -> dict:
    """Return an axis to its origin (homing)."""
    index = AXES[axis]
    rail.home(index)
    if not wait:
        return {"ok": True, "started": True}
    seconds = await motion.wait_for_home(rail, axis, timeout_s, HOME_START_GRACE_S)
    return {"ok": True, "axis": axis, "position": _safe(rail.position, index), "seconds": seconds}


@mcp.tool()
def rail_reset_alarms(clear_history: bool = True) -> dict:
    """Reset the emergency-stop / alarm latch and optionally clear the alarm history."""
    rail.reset_estop()
    if clear_history:
        rail.clear_alarm_history()
    return {"ok": True}


@mcp.tool()
def turntable_rotate(
    direction: Literal["forward", "backward"],
    speed: Annotated[float | None, Field(description="Rotate speed; omit to keep the current setting")] = None,
) -> dict:
    """Start continuous turntable rotation. Runs until turntable_stop."""
    if speed is not None:
        _in_range("speed", speed, rail.rotate_speed_range())
        rail.set_rotate_speed(speed)
    rail.rotate(direction == "forward")
    return {"ok": True, "direction": direction, "speed": speed}


@mcp.tool()
def turntable_swing(
    speed: Annotated[float, Field(description="Swing speed (see rail_get_ranges)")],
) -> dict:
    """Start turntable back-and-forth swing motion. Runs until turntable_stop."""
    _in_range("speed", speed, rail.swing_speed_range())
    rail.set_swing_speed(speed)
    rail.swing()
    return {"ok": True, "speed": speed}


@mcp.tool()
def turntable_stop() -> dict:
    """Stop turntable rotation or swing."""
    rail.stop_rotate()
    return {"ok": True}


# =============================================================================
# Start-up
# =============================================================================


def _use_config(path: Path | None) -> None:
    global cfg, lights
    cfg = config.load(path)
    lights = make_light_bank(cfg)
    cli.log_config(cfg, path, lights.backend)


def main() -> None:
    global plc_ip
    args = cli.parse_args(__doc__)
    log_file = logs.setup(args.log_dir, getattr(logging, args.log_level))
    cli.log_banner(args.transport, args.host, args.port, log_file)
    _use_config(args.lhm_config)
    plc_ip = args.plc_ip
    if not args.no_self_check:
        selfcheck.run(rail, lights, cfg, plc_ip)
    try:
        # On stdio the SDK diverts fd 1 to stderr while serving, so any printf/cout
        # output from the vendor DLLs cannot corrupt the JSON-RPC stream.
        if args.transport == "stdio":
            mcp.run()
        else:
            mcp.run(args.transport, host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    except Exception:
        logs.mcp_log.exception("Server stopped with an error")
        if cli.is_frozen():
            input("Press Enter to close...")
        raise
    finally:
        lights.close_all()
        if rail.connected:
            rail.disconnect()
        logs.mcp_log.info("Server stopped")


if __name__ == "__main__":
    main()
