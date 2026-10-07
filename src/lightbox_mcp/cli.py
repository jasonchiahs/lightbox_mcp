"""Command line of the server: arguments, which LHMconfig.ini to use, start-up banner."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

from . import selfcheck
from .config import EMBEDDED_CONFIG, LhmConfig
from .logs import mcp_log


def is_frozen() -> bool:
    """True when running as the PyInstaller-built lightbox_mcp.exe."""
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """The exe's directory, or the working directory when run from source."""
    return Path(sys.executable).parent if is_frozen() else Path.cwd()


def default_config() -> Path:
    """$LIGHTBOX_LHM_CONFIG, else LHMconfig.ini next to the exe, else the embedded copy."""
    env = os.environ.get("LIGHTBOX_LHM_CONFIG")
    if env:
        return Path(env)
    beside = base_dir() / "LHMconfig.ini"
    return beside if beside.is_file() else EMBEDDED_CONFIG


def parse_args(description: str | None, argv: list[str] | None = None) -> argparse.Namespace:
    frozen = is_frozen()
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default=os.environ.get("LIGHTBOX_MCP_TRANSPORT", "streamable-http" if frozen else "stdio"),
        help="stdio when an agent launches the server; streamable-http to serve agents over the network",
    )
    parser.add_argument("--host", default=os.environ.get("LIGHTBOX_MCP_HOST", "0.0.0.0" if frozen else "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LIGHTBOX_MCP_PORT", "8000")))
    parser.add_argument("--log-dir", type=Path, default=base_dir() / "logs")
    parser.add_argument(
        "--lhm-config",
        type=Path,
        default=default_config(),
        help="SW_MLB's LHMconfig.ini (COM ports, addresses, ranges). Default: "
        "$LIGHTBOX_LHM_CONFIG, else LHMconfig.ini next to the exe, else the embedded copy",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING"],
        default="INFO",
        help="DEBUG also logs pings and the status polling during moves",
    )
    parser.add_argument(
        "--plc-ip",
        default=os.environ.get("LIGHTBOX_PLC_IP", selfcheck.PLC_IP),
        help="rail/turntable PLC address used by the start-up check (SW_MLB uses 192.168.1.1)",
    )
    parser.add_argument(
        "--no-self-check",
        action="store_true",
        help="skip connecting the rail PLC and light ports at start-up",
    )
    return parser.parse_args(argv)


def log_banner(transport: str, host: str, port: int, log_file: Path) -> None:
    mcp_log.info("MLB-LHM lightbox MCP server starting (transport=%s)", transport)
    if transport != "stdio":
        path = "/mcp" if transport == "streamable-http" else "/sse"
        if host in ("0.0.0.0", "::"):
            try:
                addresses = sorted(set(socket.gethostbyname_ex(socket.gethostname())[2]))
            except OSError:
                addresses = []
            for ip in ["127.0.0.1", *addresses]:
                mcp_log.info("  agent URL: http://%s:%d%s", ip, port, path)
        else:
            mcp_log.info("  agent URL: http://%s:%d%s", host, port, path)
    mcp_log.info("  log file : %s", log_file)


def log_config(cfg: LhmConfig, path: Path | None, backend: str) -> None:
    origin = "embedded in the exe" if path == EMBEDDED_CONFIG else "external"
    mcp_log.info("  config   : %s (%s)", cfg.source or "built-in defaults (no LHMconfig.ini)", origin)
    for name, light in cfg.lights.items():
        mcp_log.info("    %-4s light: address %d, port %s", name, light.address, light.port or "-")
    mcp_log.info(
        "    flicker CCT flag: %d, DeviceVersion %s -> lights via %s", cfg.has_cct, cfg.device_version, backend
    )
    for warning in cfg.warnings:
        mcp_log.warning("  config   : %s", warning)
