"""Traffic logging: agent <-> server (MCP), server <-> lights (raw serial bytes)
and server <-> rail PLC (DLL calls).

Every line goes to the console and to a daily-rotated file, e.g.

    10:21:03.512 INFO  [mcp]   agent 10.0.0.5 --> tools/call light_set_white {"light": "top", ...}
    10:21:03.525 INFO  [light] top COM24 TX 55 01 04 88 13 64 19 8D  (addr=1 illuminance=5000 cct=6500)
    10:21:03.931 INFO  [rail]  TX GetCurrentPosition(0, &0.0)
    10:21:03.940 INFO  [rail]  RX GetCurrentPosition = 0  out=812.5  (9.1 ms)
    10:21:04.541 INFO  [mcp]   agent 10.0.0.5 <-- tools/call light_set_white OK {"ok": true, ...}  (1029.0 ms)
"""

from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path
from typing import Any

mcp_log = logging.getLogger("lightbox.mcp")
light_log = logging.getLogger("lightbox.light")
rail_log = logging.getLogger("lightbox.rail")
check_log = logging.getLogger("lightbox.check")

_MAX_TEXT = 4000
_COLORS = {"DEBUG": "\x1b[90m", "WARNING": "\x1b[33m", "ERROR": "\x1b[31m", "CRITICAL": "\x1b[41m"}
_CHANNEL_COLORS = {"mcp": "\x1b[36m", "light": "\x1b[35m", "rail": "\x1b[32m", "check": "\x1b[97m"}
# Self-check lines carry extra={"status": ...}; on the console the whole message takes this colour.
_STATUS_COLORS = {"ok": "\x1b[1;92m", "fail": "\x1b[1;91m", "warn": "\x1b[1;93m", "info": "\x1b[97m"}
_RESET = "\x1b[0m"


class _Formatter(logging.Formatter):
    def __init__(self, color: bool, with_date: bool) -> None:
        super().__init__()
        self.color = color
        self.with_date = with_date

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%Y-%m-%d %H:%M:%S" if self.with_date else "%H:%M:%S", time.localtime(record.created))
        ts = f"{ts}.{int(record.msecs):03d}"
        channel = record.name.rsplit(".", 1)[-1]
        level = f"{record.levelname:<5}"
        tag = f"[{channel}]".ljust(7)
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        status = getattr(record, "status", None)
        if self.color and status in _STATUS_COLORS:
            msg = _STATUS_COLORS[status] + msg + _RESET
        if self.color:
            level = _COLORS.get(record.levelname, "") + level + _RESET
            tag = _CHANNEL_COLORS.get(channel, "") + tag + _RESET
        return f"{ts} {level} {tag} {msg}"


def _enable_ansi() -> bool:
    if not sys.stderr.isatty():
        return False
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # VT processing
    except OSError:
        return False


def setup(log_dir: Path, level: int = logging.INFO) -> Path:
    """Log lightbox.* to stderr (console) and to <log_dir>/lightbox_mcp.log."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "lightbox_mcp.log"

    root = logging.getLogger("lightbox")
    root.setLevel(level)
    root.propagate = False
    root.handlers.clear()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(_Formatter(color=_enable_ansi(), with_date=False))
    root.addHandler(console)

    file = logging.handlers.TimedRotatingFileHandler(log_file, when="midnight", backupCount=30, encoding="utf-8")
    file.setFormatter(_Formatter(color=False, with_date=True))
    root.addHandler(file)
    return log_file


def clip(text: str) -> str:
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + f"... (+{len(text) - _MAX_TEXT} chars)"


def one_line(text: str) -> str:
    """Compact JSON text, or join the lines of any other text, so each log entry is one line."""
    try:
        return to_json(json.loads(text))
    except (TypeError, ValueError):
        return clip(" | ".join(line.strip() for line in text.splitlines() if line.strip()))


def to_json(value: Any) -> str:
    try:
        return clip(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return clip(repr(value))


# --- rendering of ctypes call arguments -------------------------------------


def _is_byref(value: Any) -> bool:
    """True for ctypes.byref(...) results (their type is not exposed by ctypes)."""
    return type(value).__name__ == "CArgObject"


def render(value: Any) -> Any:
    """Readable form of a ctypes argument (arrays → lists, byref → pointee)."""
    if isinstance(value, ctypes.Array):
        return [render(v) for v in value]
    if isinstance(value, ctypes.Structure):
        return {name: render(getattr(value, name)) for name, *_ in value._fields_}
    if isinstance(value, ctypes._SimpleCData):
        return render(value.value)
    if _is_byref(value):
        return f"&{render(value._obj)}"
    if isinstance(value, bytes):
        if len(value) == 1:  # c_char fields such as dataPram_New.mode
            return value[0]
        return value.decode("gbk", errors="replace")
    if isinstance(value, float):
        return round(value, 6)
    return value


def render_call(name: str, args: tuple) -> str:
    parts = []
    for arg in args:
        shown = render(arg)
        parts.append(repr(shown) if isinstance(shown, str) and not shown.startswith("&") else str(shown))
    return f"{name}({', '.join(parts)})"


def outputs(args: tuple) -> list:
    """Values of byref() out-parameters after a call."""
    return [render(a._obj) for a in args if _is_byref(a)]


def traced(logger: logging.Logger, name: str, fn, args: tuple, level: int = logging.INFO):
    """Call a DLL function, logging the request (TX) and the result (RX)."""
    logger.log(level, "TX %s", render_call(name, args))
    start = time.perf_counter()
    try:
        result = fn(*args)
    except Exception:
        logger.exception("RX %s raised after %.1f ms", name, (time.perf_counter() - start) * 1000)
        raise
    elapsed = (time.perf_counter() - start) * 1000
    outs = outputs(args)
    shown = "void" if result is None else render(result)
    extra = f"  out={outs[0] if len(outs) == 1 else outs}" if outs else ""
    failed = isinstance(result, int) and not isinstance(result, bool) and result != 0
    logger.log(
        max(level, logging.WARNING) if failed else level,
        "RX %s = %s%s  (%.1f ms)",
        name,
        shown,
        extra,
        elapsed,
    )
    return result
