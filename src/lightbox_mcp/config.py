"""Reading the vendor's LHMconfig.ini (the SW_MLB app's device configuration).

Point the server at the copy next to SW_MLB.exe on the lightbox PC to reuse
its COM ports, light addresses, CCT flag and parameter ranges. Without a file,
the defaults below (the values shipped with SW_MLB V2.7) apply.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

LIGHTS = ("top", "up", "down")
# Built into the package (and the exe); a file next to the exe or --lhm-config overrides it.
EMBEDDED_CONFIG = Path(__file__).parent / "LHMconfig.ini"

_COM = re.compile(r"COM(\d+)", re.IGNORECASE)

# Per light: [Connect] port key, [LightAddress] address key, default address.
_LIGHT_KEYS = {
    "top": ("TopComName", "Top_ADDR", 1),
    "up": ("UpComName", "Up_ADDR", 2),
    "down": ("DownComName", "Down_ADDR", 3),
}

# (section, default) per parameter, for the top light and the two flicker (up/down) lights.
_RANGE_KEYS = {
    "top": {
        "illuminance": ("TopIlluminanceRange", (0, 60000)),
        "cct": ("TopCCTRange", (2300, 10000)),
        "ir_850": ("Top850nmIlluminanceRange", (0, 60000)),
        "ir_940": ("Top940nmIlluminanceRange", (0, 60000)),
    },
    "flicker": {
        "illuminance": ("FlickerIlluminanceRange", (0, 60000)),
        "cct": ("FlickerCCTRange", (2300, 10000)),
        "frequency": ("FlickerFrequencyRange", (0, 1000)),
        "ir_850": ("Flicker850nmIlluminanceRange", (0, 60000)),
        "ir_940": ("Flicker940nmIlluminanceRange", (0, 60000)),
        "duty": ("FlickerDutyRange", (1, 99)),
    },
}


def com_port_number(name: str) -> int | None:
    """n for a Windows port name "COMn" (any case, surrounding spaces ignored), else None."""
    match = _COM.fullmatch(name.strip())
    return int(match.group(1)) if match else None


def normalize_port(port: str) -> str:
    """Upper-case COM port names (com4 -> COM4); pyserial URLs such as loop:// stay as they are."""
    return port.upper() if port.lower().startswith("com") else port


@dataclass
class LightSettings:
    name: str
    address: int
    port: str | None = None


@dataclass
class LhmConfig:
    lights: dict[str, LightSettings] = field(default_factory=dict)
    has_cct: bool = True
    # [DeviceInfo] DeviceVersion: SW_MLB drives the lights through CCXL_calibrate.dll for
    # V1/V1.1/V1.2 and through the LHM serial protocol for V2.
    device_version: str = "V1.2"
    ranges: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    source: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def uses_lhm_serial(self) -> bool:
        """SW_MLB's rule (LHM_V2_Factory vs CCXL_V5_Factory)."""
        return self.device_version == "V2"

    def range(self, light: str, param: str) -> tuple[int, int]:
        return self.ranges["top" if light == "top" else "flicker"][param]


def load(path: Path | None) -> LhmConfig:
    cfg = LhmConfig(
        lights={name: LightSettings(name, address) for name, (_, _, address) in _LIGHT_KEYS.items()},
        ranges={group: {p: default for p, (_, default) in keys.items()} for group, keys in _RANGE_KEYS.items()},
    )
    if path is None:
        return cfg
    if not path.is_file():
        cfg.warnings.append(f"{path} not found; using built-in defaults")
        return cfg

    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str  # keys are case-sensitive in QSettings files
    ini.read(path, encoding="utf-8-sig")
    cfg.source = path

    for name, (port_key, address_key, _) in _LIGHT_KEYS.items():
        light = cfg.lights[name]
        port = ini.get("Connect", port_key, fallback="").strip()
        if com_port_number(port) is not None:
            light.port = port.upper()
        elif port:
            cfg.warnings.append(f"[Connect] {port_key}={port!r} is not a COM port name; ignored")
        light.address = ini.getint("LightAddress", address_key, fallback=light.address)
    cfg.has_cct = ini.getint("FlickerLightParamFlag", "CCT", fallback=1) != 0
    cfg.device_version = ini.get("DeviceInfo", "DeviceVersion", fallback=cfg.device_version).strip()

    for group, keys in _RANGE_KEYS.items():
        for param, (section, default) in keys.items():
            lo = ini.getint(section, "Min", fallback=default[0])
            hi = ini.getint(section, "Max", fallback=default[1])
            cfg.ranges[group][param] = (lo, hi)
    return cfg
