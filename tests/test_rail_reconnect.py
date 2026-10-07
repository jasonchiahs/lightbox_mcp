"""The PLC drops idle connections; RailController must reconnect once and retry."""

from lightbox_mcp.rail import AXIS_RAIL, RailController


class FakeRail(RailController):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.calls: list[str] = []
        self.connected, self.ip = True, "192.168.1.1"

    def _raw(self, name, *args, require_connection=True):
        self.calls.append(name)
        if name == "InitAndConnect":
            return 0
        if name == "GetCurrentPosition":
            if self.failures:
                self.failures -= 1
                return 2
            args[-1]._obj.value = 812.5
            return 0
        return 0

    def disconnect(self) -> None:
        self.calls.append("Disconnect")
        self.connected = False
        self.ip = None

    def message(self, code):
        return "Error! Read Data Failed."


def test_stale_connection_is_reopened_once():
    rail = FakeRail(failures=1)
    assert rail.position(AXIS_RAIL) == 812.5
    assert rail.calls == ["GetCurrentPosition", "Disconnect", "InitAndConnect", "GetCurrentPosition"]
    assert rail.connected and rail.ip == "192.168.1.1"


def test_persistent_failure_still_raises():
    rail = FakeRail(failures=5)
    try:
        rail.position(AXIS_RAIL)
    except Exception as exc:
        assert "code 2" in str(exc)
    else:
        raise AssertionError("expected RailError")
    assert rail.calls.count("InitAndConnect") == 1  # only one reconnect attempt
