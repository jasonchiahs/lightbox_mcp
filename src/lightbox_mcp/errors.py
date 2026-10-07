"""Error types surfaced to MCP clients.

Subclassing ToolError makes the SDK forward the message to the agent instead
of masking it as a generic crash.
"""

from mcp.server.mcpserver.exceptions import ToolError


class DeviceError(ToolError):
    """A device is unavailable, not connected, or rejected a command."""


def light_not_connected(name: str) -> DeviceError:
    return DeviceError(f"The {name} light is not connected; call light_connect first.")
