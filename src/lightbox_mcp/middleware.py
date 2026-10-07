"""MCP middleware that logs the traffic between the agents and the server."""

from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import BaseModel

from . import logs


class TrafficLogger:
    """MCP middleware: log every request from the agent and the reply sent back."""

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        method = ctx.method
        params = dict(ctx.params or {})
        params.pop("_meta", None)
        quiet = method == "ping" or method.startswith("notifications/")
        level = logging.DEBUG if quiet else logging.INFO
        peer = _peer(ctx)
        if method == "tools/call":
            label, shown = f"tools/call {params.get('name')}", params.get("arguments") or {}
        else:
            label, shown = method, params
        logs.mcp_log.log(level, "%s --> %s %s", peer, label, logs.to_json(shown))

        start = time.perf_counter()
        try:
            result = await call_next(ctx)
        except Exception as exc:
            logs.mcp_log.warning(
                "%s <-- %s FAILED %s: %s  (%.1f ms)",
                peer,
                label,
                type(exc).__name__,
                exc,
                (time.perf_counter() - start) * 1000,
            )
            raise
        if ctx.request_id is None:
            return result

        payload = (
            result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(result, BaseModel)
            else (result or {})
        )
        status = "OK"
        if method == "tools/call":
            if payload.get("isError") or payload.get("is_error"):
                status, level = "ERROR", logging.WARNING
            texts = [c.get("text") for c in payload.get("content", []) if c.get("type") == "text"]
            reply = logs.one_line(" ".join(t for t in texts if t))
        elif method == "initialize":
            reply = logs.to_json({k: payload.get(k) for k in ("protocolVersion", "serverInfo")})
        elif method == "tools/list":
            reply = f"{len(payload.get('tools', []))} tools"
        else:
            reply = logs.to_json(payload)
        logs.mcp_log.log(
            level,
            "%s <-- %s %s %s  (%.1f ms)",
            peer,
            label,
            status,
            reply,
            (time.perf_counter() - start) * 1000,
        )
        return result


def _peer(ctx: Any) -> str:
    request = getattr(ctx, "request", None)
    client = getattr(request, "client", None)
    return f"agent {client.host}" if client else "agent"
