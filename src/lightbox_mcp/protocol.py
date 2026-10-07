"""Serial frames for the MLB-LHM V2.0 lights, byte-for-byte as the vendor DLLs build them.

Reverse-engineered from LHM_TopLight.dll / LHM_FlikerLight.dll (SW_MLB V2.7) and
checked against those DLLs by tests/test_protocol_vs_dll.py.

Every command is one 8-byte frame, written at 9600 baud 8N1, with no reply:

    55 | addr | cmd | p0 | p1 | p2 | p3 | checksum

checksum = ~(sum of the first 7 bytes) & 0xFF. 16-bit values are little-endian.
"""

from __future__ import annotations

HEADER = 0x55
BAUD_RATE = 9600

CMD_MODE = 0x01  # p0 = mode (1 white, 2 infrared, 3 flicker)
CMD_LUX = 0x02  # flicker light without CCT: p0p1 = illuminance
CMD_LUX_KELVIN = 0x04  # p0p1 = illuminance, p2p3 = colour temperature
CMD_IR = 0x06  # top light: p0p1 = 850 nm level, p2p3 = 940 nm level
CMD_FREQ_DUTY = 0xF9  # flicker light: p0p1 = frequency; p2 = 1, p3 = duty (only without CCT)

MODE_WHITE = 1
MODE_INFRARED = 2
MODE_FLICKER = 3


def _u16(value: int) -> tuple[int, int]:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"value {value} does not fit in 16 bits")
    return value & 0xFF, value >> 8


def frame(addr: int, cmd: int, p0: int = 0, p1: int = 0, p2: int = 0, p3: int = 0) -> bytes:
    body = bytes([HEADER, addr & 0xFF, cmd & 0xFF, p0 & 0xFF, p1 & 0xFF, p2 & 0xFF, p3 & 0xFF])
    return body + bytes([~sum(body) & 0xFF])


def set_mode(addr: int, mode: int) -> bytes:
    """SetLightMode(mode, addr) — same frame for the top and the flicker lights."""
    return frame(addr, CMD_MODE, mode)


def top_lux_kelvin(addr: int, lux: int, cct: int) -> bytes:
    """TopLight::SetLuxKelvin(lux, cct, addr)."""
    return frame(addr, CMD_LUX_KELVIN, *_u16(lux), *_u16(cct))


def top_infrared(addr: int, ir850: int, ir940: int) -> bytes:
    """TopLight::SetEightAndNine(850, 940, addr)."""
    return frame(addr, CMD_IR, *_u16(ir850), *_u16(ir940))


def flicker_lux_kelvin(addr: int, lux: int, cct: int, has_cct: bool) -> bytes:
    """FlikerLight::SetLuxKelvin(lux, cct, addr, has_cct).

    has_cct is the [FlickerLightParamFlag] CCT flag of LHMconfig.ini; without
    it only the illuminance is sent (command 0x02).
    """
    if has_cct:
        return frame(addr, CMD_LUX_KELVIN, *_u16(lux), *_u16(cct))
    return frame(addr, CMD_LUX, *_u16(lux))


def flicker_freq_duty(addr: int, freq: int, duty: int, has_cct: bool) -> bytes:
    """FlikerLight::SetFrequencyDuty(freq, duty, addr, has_cct).

    Matches the DLL exactly: with has_cct set, the duty is NOT transmitted
    (bytes p2/p3 are 0); without it p2 = 1 and p3 = duty.
    """
    if has_cct:
        return frame(addr, CMD_FREQ_DUTY, *_u16(freq), 0, 0)
    return frame(addr, CMD_FREQ_DUTY, *_u16(freq), 1, duty)


def to_hex(data: bytes) -> str:
    """Bytes as upper-case hex pairs, the way frames are logged and returned: "55 01 04 ..."."""
    return data.hex(" ").upper()


def describe(data: bytes) -> str:
    """Human-readable decoding of a frame, for the logs."""
    if len(data) != 8 or data[0] != HEADER:
        return "unrecognised"
    addr, cmd, p0, p1, p2, p3 = data[1:7]
    a, b = p0 | p1 << 8, p2 | p3 << 8
    names = {
        CMD_MODE: f"mode={p0}",
        CMD_LUX: f"illuminance={a}",
        CMD_LUX_KELVIN: f"illuminance={a} cct={b}",
        CMD_IR: f"850nm={a} 940nm={b}",
        CMD_FREQ_DUTY: f"frequency={a}" + (f" duty={p3}" if p2 == 1 else " (duty not sent)"),
    }
    ok = data[7] == (~sum(data[:7]) & 0xFF)
    return f"addr={addr} " + names.get(cmd, f"cmd=0x{cmd:02X}") + ("" if ok else " BAD-CHECKSUM")
