from lightbox_mcp import config, protocol
from lightbox_mcp.lights import LightBank


def test_three_lights_share_one_port():
    bank = LightBank(config.load(None))
    for name in ("top", "up", "down"):
        bank.get(name).open("loop://")
    ports = {id(bank.get(n)._port) for n in ("top", "up", "down")}
    assert len(ports) == 1  # opened once
    shared = bank.get("top")._port

    bank.set_param("up", protocol.MODE_WHITE, 1000, 5000)
    assert bank.get("up").last_command["mode"] == 1

    bank.get("top").close()
    bank.get("up").close()
    assert shared.serial.is_open and bank.get("down").connected
    bank.get("down").close()
    assert not shared.serial.is_open
