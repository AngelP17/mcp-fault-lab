"""Scenario catalog. Add new failure modes here; the runner picks them up.

`expect` is what a well-behaved client SHOULD do, not what it currently does:
  ok         -> call returns a normal result
  tool_error -> call returns a result with is_error=True
  error      -> call raises promptly (no hang)
  timeout    -> call raises a timeout error at roughly read_timeout
  hang_info  -> no timeout configured, so blocking is arguably expected;
                reported as INFO so you can see the default behavior
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .chaos_proxy import Fault


@dataclass
class Scenario:
    id: str
    title: str
    expect: str
    tool: str = "echo"
    args: dict[str, Any] = field(default_factory=lambda: {"text": "ping"})
    fault: Fault | None = None
    read_timeout: float | None = None
    http_status: int | None = None  # status the caller ideally gets to see

    def __post_init__(self) -> None:
        if self.fault is not None and self.fault.kind in ("http", "http_jsonrpc"):
            self.http_status = self.fault.status


def _http(code: int, title: str, **headers: str) -> Scenario:
    return Scenario(f"http_{code}", title, "error", fault=Fault("http", status=code, headers=headers))


SCENARIOS: list[Scenario] = [
    Scenario("baseline_ok", "Baseline echo, no fault", "ok"),
    Scenario("tool_anticipated_failure", "Tool raises ToolError", "tool_error",
             tool="anticipated_failure", args={}),
    Scenario("tool_crash", "Tool raises an unexpected exception", "tool_error", tool="crash", args={}),
    Scenario("tool_timeout", "Tool runs past read_timeout", "timeout",
             tool="sleep", args={"seconds": 10}, read_timeout=1.5),
    _http(401, "Gateway returns 401 mid-session"),
    _http(403, "Gateway returns 403 mid-session"),
    _http(404, "Gateway returns 404 mid-session"),
    _http(429, "Gateway rate-limits with 429 + Retry-After", **{"retry-after": "2"}),
    _http(500, "Gateway returns 500"),
    _http(502, "Gateway returns 502"),
    _http(503, "Gateway returns 503 + Retry-After", **{"retry-after": "5"}),
    Scenario("http_500_jsonrpc_body", "500 carrying a JSON-RPC error body", "error",
             fault=Fault("http_jsonrpc", status=500)),
    Scenario("stall_no_timeout", "Upstream accepts and never answers, no timeout set", "hang_info",
             fault=Fault("stall")),
    Scenario("stall_with_timeout", "Upstream never answers, read_timeout=1.5s", "timeout",
             fault=Fault("stall"), read_timeout=1.5),
    Scenario("slow_within_timeout", "Upstream slow (1s) but inside a 3s timeout", "ok",
             fault=Fault("slow", delay=1.0), read_timeout=3.0),
    Scenario("truncate_sse", "Connection drops mid SSE response", "error", fault=Fault("truncate_sse")),
    Scenario("truncate_json", "Connection drops mid JSON body", "error", fault=Fault("truncate_json")),
    Scenario("garbage_json", "200 application/json with an HTML body", "error", fault=Fault("garbage_json")),
    Scenario("html_200", "200 text/html (captive portal / bad ingress)", "error", fault=Fault("html_200")),
    Scenario("empty_200", "200 application/json with empty body", "error", fault=Fault("empty_200")),
]
