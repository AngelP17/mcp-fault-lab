"""Synthetic MCP server used as the upstream behind the chaos proxy.

Every tool here is deliberately boring and synthetic. No real data, no employer
code: the point is to exercise the SDK's transport and error paths, not the tools.
"""

from __future__ import annotations

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# Marker string a crashing tool raises with. Used to check that crash details
# do NOT leak to the client (the SDK documents that they shouldn't).
CRASH_SECRET = "synthetic-crash-detail-7f3a"


def build_server() -> MCPServer:
    mcp = MCPServer("fault-lab", version="0.1.0")

    @mcp.tool()
    def echo(text: str) -> str:
        """Return the input unchanged."""
        return text

    @mcp.tool()
    async def sleep(seconds: float) -> str:
        """Sleep, then return. Used for timeout scenarios."""
        await anyio.sleep(seconds)
        return f"slept {seconds}s"

    @mcp.tool()
    def anticipated_failure() -> str:
        """Fail the documented way (ToolError -> is_error result)."""
        raise ToolError("synthetic anticipated failure")

    @mcp.tool()
    def crash() -> str:
        """Fail the undocumented way (unexpected exception)."""
        raise RuntimeError(CRASH_SECRET)

    @mcp.tool()
    def big(kb: int) -> str:
        """Return a payload of roughly `kb` kilobytes."""
        return "x" * (kb * 1024)

    return mcp
