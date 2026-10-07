"""main() wiring, and --plc-ip reaching device_check as well as the start-up check."""

import asyncio
import sys

import pytest

from lightbox_mcp import server


@pytest.fixture
def isolated_server(monkeypatch, fake_rail):
    for name in ("cfg", "lights", "plc_ip"):
        monkeypatch.setattr(server, name, getattr(server, name))  # restored after the test
    rail = fake_rail(connect_code=1)
    monkeypatch.setattr(server, "rail", rail)
    return rail


def test_device_check_uses_the_configured_plc_ip(monkeypatch, isolated_server):
    monkeypatch.setattr(server, "plc_ip", "10.9.8.7")
    result = asyncio.run(server.device_check())
    assert isolated_server.calls == [("connect", "10.9.8.7")]
    assert result["rail_ok"] is False


def test_main_uses_plc_ip_for_both_checks(monkeypatch, isolated_server, tmp_path):
    ini = tmp_path / "LHMconfig.ini"
    ini.write_text("[DeviceInfo]\nDeviceVersion=V2\n", encoding="utf-8")
    monkeypatch.setattr(server.logs, "setup", lambda log_dir, level: log_dir / "lightbox_mcp.log")
    monkeypatch.setattr(server.mcp, "run", lambda *args, **kwargs: None)
    argv = ["lightbox-mcp", "--plc-ip", "10.9.8.7", "--lhm-config", str(ini), "--log-dir", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)

    server.main()
    assert server.plc_ip == "10.9.8.7"
    assert server.cfg.source == ini and server.lights.backend.startswith("LHM serial")
    assert isolated_server.calls == [("connect", "10.9.8.7")]  # the start-up check

    asyncio.run(server.device_check())
    assert isolated_server.calls[-1] == ("connect", "10.9.8.7")
