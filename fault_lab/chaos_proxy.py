"""A tiny ASGI reverse proxy that injects HTTP-level faults into MCP traffic.

It sits between the MCP client and a real MCP server. Everything passes through
untouched (including SSE streams) unless a Fault is set, and even then the fault
only applies to `tools/call` requests, so the handshake and the recovery call in
each scenario are never affected.

This mimics what real deployments put in the middle: API gateways, auth proxies,
load balancers, and pods that die mid-response.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2

log = logging.getLogger("fault_lab.proxy")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


@dataclass
class Fault:
    """What to do to the next matching tools/call request.

    kind:
      http          -> reply `status` with a plain-text body, like a gateway would
      http_jsonrpc  -> reply `status` with a JSON-RPC error body (spec-allowed)
      stall         -> accept the request and never answer
      slow          -> wait `delay` seconds, then forward normally
      truncate_sse  -> start a 200 SSE response, then drop the connection
      truncate_json -> start a 200 JSON response with a short body, then drop
      garbage_json  -> 200 application/json whose body is HTML
      html_200      -> 200 text/html (captive portal / misrouted ingress)
      empty_200     -> 200 application/json with an empty body
    """

    kind: str
    status: int = 200
    delay: float = 0.0
    tool: str | None = None  # only fault calls to this tool; None = any tools/call
    headers: dict[str, str] = field(default_factory=dict)


class ChaosProxy:
    def __init__(self, upstream: str) -> None:
        self.upstream = upstream.rstrip("/")
        self.fault: Fault | None = None
        self.applied: list[dict[str, Any]] = []
        self._client: httpx2.AsyncClient | None = None

    def client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(timeout=None)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    # -- ASGI -----------------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        body = await _read_body(receive)
        rpc_method, tool, rpc_id = _peek_jsonrpc(scope["method"], body)

        fault = self.fault
        if fault and rpc_method == "tools/call" and (fault.tool is None or fault.tool == tool):
            self.applied.append({"kind": fault.kind, "status": fault.status, "tool": tool})
            log.info("applying fault %s to tools/call %s", fault.kind, tool)
            await self._apply(fault, scope, body, rpc_id, send)
            return
        await self._forward(scope, body, send)

    # -- faults ---------------------------------------------------------------

    async def _apply(self, fault: Fault, scope, body: bytes, rpc_id: Any, send) -> None:
        k = fault.kind
        if k == "http":
            text = f"synthetic gateway error {fault.status}\n".encode()
            await _respond(send, fault.status, text, "text/plain", fault.headers)
        elif k == "http_jsonrpc":
            payload = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32000, "message": "synthetic upstream overload"},
            }
            await _respond(send, fault.status, json.dumps(payload).encode(), "application/json", fault.headers)
        elif k == "stall":
            await anyio.sleep_forever()
        elif k == "slow":
            await anyio.sleep(fault.delay)
            await self._forward(scope, body, send)
        elif k == "truncate_sse":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body", "body": b": warming up\n\n", "more_body": True})
            raise ConnectionAbortedError("fault_lab: synthetic drop mid-SSE")
        elif k == "truncate_json":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", b"200")]})
            await send({"type": "http.response.body", "body": b'{"jsonrpc":"2.0","id":', "more_body": True})
            raise ConnectionAbortedError("fault_lab: synthetic drop mid-JSON")
        elif k == "garbage_json":
            await _respond(send, 200, b"<html><body>502 Bad Gateway</body></html>", "application/json")
        elif k == "html_200":
            await _respond(send, 200, b"<html><body>Please sign in to the network</body></html>", "text/html")
        elif k == "empty_200":
            await _respond(send, 200, b"", "application/json")
        else:
            raise ValueError(f"unknown fault kind {k!r}")

    # -- passthrough ----------------------------------------------------------

    async def _forward(self, scope, body: bytes, send) -> None:
        path = scope["path"] + (("?" + scope["query_string"].decode()) if scope.get("query_string") else "")
        headers = [(k.decode(), v.decode()) for k, v in scope["headers"] if k.decode().lower() not in HOP_BY_HOP]
        req = self.client().build_request(scope["method"], self.upstream + path, headers=headers, content=body)
        resp = await self.client().send(req, stream=True)
        try:
            out_headers = [(k.encode(), v.encode()) for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP]
            await send({"type": "http.response.start", "status": resp.status_code, "headers": out_headers})
            async for chunk in resp.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await resp.aclose()


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    return b"".join(chunks)


def _peek_jsonrpc(http_method: str, body: bytes) -> tuple[str | None, str | None, Any]:
    if http_method != "POST" or not body:
        return None, None, None
    try:
        msg = json.loads(body)
    except ValueError:
        return None, None, None
    if not isinstance(msg, dict):
        return None, None, None
    params = msg.get("params") or {}
    tool = params.get("name") if isinstance(params, dict) else None
    return msg.get("method"), tool, msg.get("id")


async def _respond(send, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
    headers = [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())]
    headers += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})
