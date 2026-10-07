from pathlib import Path

from lightbox_mcp import config

EMBEDDED = Path(__file__).resolve().parents[1] / "src" / "lightbox_mcp" / "LHMconfig.ini"


def test_defaults_without_file():
    cfg = config.load(None)
    assert {n: l.address for n, l in cfg.lights.items()} == {"top": 1, "up": 2, "down": 3}
    assert cfg.has_cct and cfg.range("top", "cct") == (2300, 10000)


def test_embedded_ini_puts_all_lights_on_com4():
    cfg = config.load(EMBEDDED)
    assert cfg.source == EMBEDDED
    assert {n: l.port for n, l in cfg.lights.items()} == {"top": "COM4", "up": "COM4", "down": "COM4"}
    assert not cfg.warnings
    assert cfg.range("up", "frequency") == (0, 1000)
    assert cfg.range("down", "duty") == (1, 99)


def test_vendor_placeholder_ports_are_ignored(tmp_path):
    ini = tmp_path / "LHMconfig.ini"  # SW_MLB V2.7 ships "1" as COM names
    ini.write_text("[Connect]\nTopComName=1\nUpComName=1\nDownComName=1\n", encoding="utf-8")
    cfg = config.load(ini)
    assert all(l.port is None for l in cfg.lights.values())
    assert len(cfg.warnings) == 3


def test_real_ports_and_flags(tmp_path):
    ini = tmp_path / "LHMconfig.ini"
    ini.write_text(
        "[Connect]\nTopComName=COM24\nUpComName=com26\nDownComName=COM25\n"
        "[LightAddress]\nTop_ADDR=4\nUp_ADDR=5\nDown_ADDR=6\n"
        "[FlickerLightParamFlag]\nCCT=0\n"
        "[TopCCTRange]\nMax=6500\nMin=3000\n",
        encoding="utf-8",
    )
    cfg = config.load(ini)
    assert {n: (l.port, l.address) for n, l in cfg.lights.items()} == {
        "top": ("COM24", 4), "up": ("COM26", 5), "down": ("COM25", 6)}
    assert cfg.has_cct is False
    assert cfg.range("top", "cct") == (3000, 6500)
    assert not cfg.warnings
