"""Standalone repro: an HTTP transport error on one tools/call tears down the whole Client.

Expected: the failing call raises MCPError (e.g. CONNECTION_CLOSED / INTERNAL_ERROR)
and the session survives for the next call, the way it already does for a truncated
SSE response, an HTML body, or a non-2xx status.

Actual (mcp 2.3.0 and main @ 2af9e30): the httpx2 TransportError escapes the
transport's task group, so `call_tool` never returns normally and an
ExceptionGroup is raised out of `async with Client(...)`.

Case A: server sends 200 application/json headers, then the connection drops mid-body
        (httpx2.RemoteProtocolError). Think: pod killed, LB idle reset, proxy crash.
Case B: server goes away entirely between two calls (httpx2.ConnectError).

Run:  python repros/transport_error_kills_client.py
"""

from __future__ import annotations

import asyncio
import json
import socket
from importlib.metadata import version

import anyio
import uvicorn
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError


def build_app():
    mcp = MCPServer("repro")

    @mcp.tool()
    def echo(text: str) -> str:
        return text

    inner = mcp.streamable_http_app(json_response=True)
    state = {"truncate": False}

    async def app(scope, receive, send):
        if scope["type"] != "http" or not state["truncate"]:
            return await inner(scope, receive, send)
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        if b'"tools/call"' not in body:  # replay to the real app untouched
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await inner(scope, replay, send)
        # 200 + Content-Length: 200, send 22 bytes, then drop the connection.
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", b"200")]})
        await send({"type": "http.response.body", "body": b'{"jsonrpc":"2.0","id":', "more_body": True})
        raise ConnectionAbortedError("repro: connection dropped mid-body")

    return app, state


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start(app, port) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical",
                                           timeout_graceful_shutdown=1))
    server.install_signal_handlers = lambda: None
    asyncio.get_running_loop().create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    return server


def show(label: str, exc: BaseException) -> None:
    leaf = exc
    while isinstance(leaf, BaseExceptionGroup):
        leaf = leaf.exceptions[0]
    print(f"  {label}: {type(exc).__name__} -> {type(leaf).__module__}.{type(leaf).__name__}: {leaf}")


async def case_a(mode: str) -> None:
    app, state = build_app()
    port = free_port()
    server = await start(app, port)
    try:
        async with Client(f"http://127.0.0.1:{port}/mcp", mode=mode) as client:
            await client.call_tool("echo", {"text": "warmup"})
            state["truncate"] = True
            try:
                with anyio.fail_after(5):
                    await client.call_tool("echo", {"text": "boom"})
                print("  call returned normally (unexpected)")
            except MCPError as e:
                print(f"  OK: call raised MCPError code={e.error.code} {e.error.message!r}")
            state["truncate"] = False
            await client.call_tool("echo", {"text": "still alive?"})
            print("  OK: session survived")
    except Exception as e:  # noqa: BLE001
        show("BUG: escaped `async with Client`", e)
    finally:
        server.should_exit = True


async def case_b(mode: str) -> None:
    app, _ = build_app()
    port = free_port()
    server = await start(app, port)
    try:
        async with Client(f"http://127.0.0.1:{port}/mcp", mode=mode) as client:
            await client.call_tool("echo", {"text": "warmup"})
            server.should_exit = True
            await asyncio.sleep(1.5)  # let it fully stop
            try:
                with anyio.fail_after(5):
                    await client.call_tool("echo", {"text": "anyone home?"})
                print("  call returned normally (unexpected)")
            except MCPError as e:
                print(f"  OK: call raised MCPError code={e.error.code} {e.error.message!r}")
    except Exception as e:  # noqa: BLE001
        show("BUG: escaped `async with Client`", e)


async def main() -> None:
    print(f"mcp {version('mcp')}, httpx2 {version('httpx2')}")
    for mode in ("auto", "legacy"):
        print(f"[case A: dropped mid-body, mode={mode}]")
        await case_a(mode)
        print(f"[case B: server gone, mode={mode}]")
        await case_b(mode)
    await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
