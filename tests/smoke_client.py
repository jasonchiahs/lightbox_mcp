"""Hardware-free end-to-end test: drive the server as an MCP client.

Lights are connected to pyserial loop:// ports (writes are echoed back), the
rail is only queried for its static ranges. Nothing touches real hardware.

    uv run python tests/smoke_client.py                          # spawns the server over stdio
    uv run python tests/smoke_client.py http://127.0.0.1:8000/mcp   # a running server started with DeviceVersion=V2
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def transport():
    if len(sys.argv) > 1:
        async with streamable_http_client(sys.argv[1]) as streams:
            yield streams[0], streams[1]
    else:
        # The loop:// light checks exercise the LHM serial backend (DeviceVersion V2).
        ini = Path(tempfile.mkdtemp()) / "LHMconfig.ini"
        ini.write_text("[DeviceInfo]\nDeviceVersion=V2\n", encoding="utf-8")
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "lightbox_mcp.server", "--no-self-check", "--lhm-config", str(ini)])
        async with stdio_client(params) as streams:
            yield streams[0], streams[1]


async def call(session, tool, args=None, expect_error=False):
    res = await session.call_tool(tool, args or {})
    text = res.content[0].text if res.content else ""
    print(f"{tool} {json.dumps(args or {})} -> {'ERROR ' if res.is_error else ''}{text[:300]}")
    assert res.is_error == expect_error, f"{tool}: expected error={expect_error}"
    return json.loads(text) if not res.is_error else text


async def main() -> None:
    async with transport() as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"server {init.server_info.name}: {len(tools)} tools")

            await call(session, "light_set_white", {"light": "top", "cct": 5000, "illuminance": 1000}, expect_error=True)
            await call(session, "light_connect", {"top_port": "loop://", "up_port": "loop://", "down_port": "loop://"})
            top = await call(session, "light_set_white", {"light": "top", "cct": 6500, "illuminance": 5000})
            assert top["frames"] == ["55 01 04 88 13 64 19 8D", "55 01 06 00 00 00 00 A3", "55 01 01 01 00 00 00 A7"], top
            await call(session, "light_set_infrared", {"ir_850": 1000, "ir_940": 2000})
            await call(session, "light_set_flicker", {"light": "up", "frequency": 60, "duty": 50, "illuminance": 3000})
            status = await call(session, "light_status")
            assert status["lights"]["up"]["last_command"]["mode"] == 3
            await call(session, "light_set_white", {"light": "down", "cct": 9000, "illuminance": 70000}, expect_error=True)
            await call(session, "light_set_flicker", {"light": "down", "frequency": 2000, "duty": 50, "illuminance": 1}, expect_error=True)
            off = await call(session, "light_off", {"light": "all"})
            assert off["off"] == ["top", "up", "down"]
            status = await call(session, "light_status")
            assert all(status["lights"][n]["last_command"]["off"] for n in ("top", "up", "down"))
            await call(session, "light_disconnect")

            ranges = await call(session, "rail_get_ranges")
            assert ranges["rail_position"] == [0.0, 1750.0]
            status = await call(session, "rail_get_status")
            assert "not connected" in status["rail"]["ready"]
            assert "not connected" in status["turntable"]["position_deg"]
            await call(session, "get_realtime", expect_error=True)
    print("OK")


asyncio.run(main())
