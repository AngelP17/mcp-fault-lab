"""Run every fault scenario against a real MCP server through the chaos proxy.

    python -m fault_lab.run                      # all scenarios, both protocol modes
    python -m fault_lab.run --only http_401 stall_with_timeout
    python -m fault_lab.run --modes legacy --hang-budget 10
    python -m fault_lab.run --otlp http://localhost:4318   # also ship traces to Jaeger/Tempo

Writes report.md (human) and report.json (machine) to --out.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

import anyio
import uvicorn
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPX2ClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from .chaos_proxy import ChaosProxy
from .scenarios import SCENARIOS, Scenario
from .server import CRASH_SECRET, build_server

EXPORTER = InMemorySpanExporter()


# --------------------------------------------------------------------------- setup


def setup_tracing(otlp: str | None) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": "mcp-fault-lab"}))
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    if otlp:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp.rstrip("/") + "/v1/traces")))
    trace.set_tracer_provider(provider)
    HTTPX2ClientInstrumentor().instrument()


class LogCollector(logging.Handler):
    """Keeps WARNING+ records from the SDK so we can see errors that were logged instead of raised."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("mcp"):
            self.records.append(record)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def serve(app: Any, port: int, lifespan: str) -> uvicorn.Server:
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical",
                         lifespan=lifespan, timeout_graceful_shutdown=1)
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    asyncio.get_running_loop().create_task(server.serve())
    for _ in range(250):
        if server.started:
            return server
        await asyncio.sleep(0.02)
    raise RuntimeError(f"server on :{port} did not start")


# --------------------------------------------------------------------------- one run


def describe_exc(e: BaseException) -> dict[str, Any]:
    d: dict[str, Any] = {"type": f"{type(e).__module__}.{type(e).__qualname__}", "message": str(e)[:400]}
    err = getattr(e, "error", None)
    if err is not None:
        d["code"] = getattr(err, "code", None)
        d["rpc_message"] = getattr(err, "message", None)
        d["data"] = getattr(err, "data", None)
    if isinstance(e, BaseExceptionGroup):
        d["group"] = [describe_exc(x) for x in e.exceptions]
    return d


def span_dict(s: ReadableSpan) -> dict[str, Any]:
    return {
        "name": s.name,
        "kind": s.kind.name,
        "status": s.status.status_code.name,
        "status_description": s.status.description,
        "trace_id": f"{s.context.trace_id:032x}",
        "span_id": f"{s.context.span_id:016x}",
        "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
        "attributes": {k: v for k, v in (s.attributes or {}).items()},
        "duration_ms": round(((s.end_time or 0) - (s.start_time or 0)) / 1e6, 1),
    }


async def call_once(client: Client, sc: Scenario, budget: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    out: dict[str, Any]
    try:
        with anyio.fail_after(budget):
            res = await client.call_tool(sc.tool, sc.args, read_timeout_seconds=sc.read_timeout)
        text = " ".join(getattr(c, "text", "") for c in res.content)[:300]
        out = {"outcome": "tool_error" if res.is_error else "ok", "result_text": text}
    except TimeoutError:
        out = {"outcome": "hang"}
    except MCPError as e:
        out = {"outcome": "timeout" if e.error.code == REQUEST_TIMEOUT else "error", "exception": describe_exc(e)}
    except Exception as e:  # noqa: BLE001 - we want to see exactly what leaks out
        out = {"outcome": "error", "exception": describe_exc(e)}
    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return out


async def run_scenario(sc: Scenario, mode: str, url: str, proxy: ChaosProxy,
                       logs: LogCollector, budget: float) -> dict[str, Any]:
    run: dict[str, Any] = {"scenario": sc.id, "title": sc.title, "mode": mode, "expect": sc.expect}
    proxy.fault = None
    proxy.applied.clear()
    EXPORTER.clear()
    logs.records.clear()
    teardown_clean = False
    target_spans: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    try:
        with anyio.move_on_after(budget * 3) as outer:
            async with Client(url, mode=mode, cache=None) as client:
                run["protocol_version"] = client.protocol_version
                with anyio.fail_after(budget):
                    await client.call_tool("echo", {"text": "warmup"})
                EXPORTER.clear()

                proxy.fault = sc.fault
                t0 = time.perf_counter()
                run.update(await call_once(client, sc, budget))
                proxy.fault = None
                run["fault_applied"] = bool(proxy.applied)
                target_spans = [span_dict(s) for s in EXPORTER.get_finished_spans()]
                run["logged_errors"] = [f"{r.name}: {r.getMessage()[:160]}" for r in logs.records]

                # Does the same session still work once the fault clears?
                rec = await call_once(client, Scenario("recovery", "recovery", "ok"), budget)
                run["recovered"] = rec["outcome"] == "ok"
                run["recovery"] = rec
            teardown_clean = True
        run["teardown_clean"] = teardown_clean and not outer.cancelled_caught
    except Exception as e:  # noqa: BLE001
        # An error escaped the `async with Client` block itself: the transport's task
        # group died and took the whole session (and the in-flight call) down with it.
        run["client_context_error"] = describe_exc(e)
        run["teardown_clean"] = True
        run.setdefault("outcome", "client_crashed")
        run.setdefault("elapsed_s", round(time.perf_counter() - t0, 2))
        run["fault_applied"] = bool(proxy.applied)
        run["recovered"] = False
        run["recovery"] = "client context already dead"
        run["logged_errors"] = [f"{r.name}: {r.getMessage()[:160]}" for r in logs.records]
        target_spans = target_spans or [span_dict(s) for s in EXPORTER.get_finished_spans()]
    proxy.fault = None

    # Late-finishing spans (e.g. the server span of a stalled call) arrive after teardown.
    late = [span_dict(s) for s in EXPORTER.get_finished_spans()]
    run["spans"] = target_spans
    client_span = next((s for s in target_spans if s["kind"] == "CLIENT" and s["name"].startswith("MCP send tools/call")), None)
    run["client_span"] = client_span
    if client_span:
        tid = client_span["trace_id"]
        pool = list({s["span_id"]: s for s in target_spans + late}.values())
        run["server_span"] = next((s for s in pool if s["kind"] == "SERVER" and s["trace_id"] == tid), None)
        run["http_spans"] = [s for s in pool if s["kind"] == "CLIENT" and s["trace_id"] == tid
                             and s["name"] != client_span["name"]]
    run["checks"] = evaluate(sc, run)
    return run


# --------------------------------------------------------------------------- checks


def chk(level: str, name: str, detail: str) -> dict[str, str]:
    return {"level": level, "check": name, "detail": detail}


def evaluate(sc: Scenario, r: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    oc = r.get("outcome")
    exc = r.get("exception") or {}

    if sc.fault is not None and not r.get("fault_applied"):
        out.append(chk("FAIL", "fault_not_applied", "harness problem: proxy never saw the tools/call"))

    if r.get("client_context_error"):
        leaf = r["client_context_error"]
        while leaf.get("group"):
            leaf = leaf["group"][0]
        out.append(chk("FAIL", "transport_error_kills_client",
                       f"a fault on ONE request escaped `async with Client` as {leaf['type']}: {leaf['message'][:160]} "
                       f"(the call itself ended as {oc!r}); the whole session is gone instead of that call failing"))
        return out

    if oc == "hang":
        lvl = "INFO" if sc.expect == "hang_info" else "FAIL"
        swallowed = " SDK logged an error but never raised it to the caller." if r.get("logged_errors") else ""
        out.append(chk(lvl, "caller_hangs", f"call blocked for the full hang budget ({r['elapsed_s']}s).{swallowed}"))
    elif sc.expect != "hang_info" and oc != sc.expect:
        out.append(chk("FAIL", "unexpected_outcome", f"expected {sc.expect}, got {oc}: {exc or r.get('result_text')}"))

    if sc.expect == "timeout" and oc == "timeout" and sc.read_timeout and r["elapsed_s"] > sc.read_timeout * 2:
        out.append(chk("WARN", "slow_timeout", f"timeout fired at {r['elapsed_s']}s for a {sc.read_timeout}s budget"))

    if sc.http_status and oc == "error":
        blob = json.dumps(exc, default=str)
        if str(sc.http_status) not in blob:
            out.append(chk("WARN", "http_status_lost",
                           f"caller sees code={exc.get('code')} message={exc.get('rpc_message') or exc.get('message')!r}; "
                           f"HTTP {sc.http_status} is not in the exception message or data"))

    if sc.fault and sc.fault.kind == "http_jsonrpc" and oc == "error":
        if "synthetic upstream overload" not in json.dumps(exc, default=str):
            out.append(chk("WARN", "jsonrpc_body_dropped", "server's JSON-RPC error body was replaced by a generic error"))

    if sc.id == "tool_crash" and CRASH_SECRET in (r.get("result_text") or "") + json.dumps(exc, default=str):
        out.append(chk("FAIL", "crash_detail_leaked", "internal exception text reached the client"))

    cs = r.get("client_span")
    if oc in ("error", "timeout", "hang"):
        if cs is None:
            out.append(chk("WARN", "no_client_span", "no finished CLIENT span for the failed tools/call"))
        elif cs["status"] != "ERROR":
            out.append(chk("WARN", "client_span_not_error",
                           f"client span status is {cs['status']} although the call failed ({oc})"))
    if oc == "tool_error" and cs and cs["status"] == "ERROR":
        out.append(chk("INFO", "tool_error_marks_client_span", "client span ERROR for an is_error result"))

    if cs and sc.fault and sc.fault.kind in ("http", "http_jsonrpc"):
        codes = [s["attributes"].get("http.response.status_code") or s["attributes"].get("http.status_code")
                 for s in r.get("http_spans") or []]
        if not r.get("http_spans"):
            out.append(chk("WARN", "http_span_not_linked", "no HTTP client span in the same trace as the MCP span"))
        elif sc.http_status not in codes:
            out.append(chk("INFO", "http_span_status", f"HTTP span status codes in trace: {codes}"))

    if not r.get("recovered", False):
        out.append(chk("WARN", "session_not_recovered",
                       f"same session failed after the fault cleared: {r.get('recovery')}"))
    if not r.get("teardown_clean"):
        out.append(chk("WARN", "teardown_hang", "closing the client did not finish within the budget"))
    return out


def cross_checks(runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for mode in sorted({r["mode"] for r in runs}):
        by = {r["scenario"]: r for r in runs if r["mode"] == mode}

        def sig(sid: str) -> tuple | None:
            e = (by.get(sid) or {}).get("exception") or {}
            return (e.get("type"), e.get("code"), e.get("rpc_message")) if e else None

        base = sig("http_500")
        same = [s for s in ("http_401", "http_403", "http_429", "http_502", "http_503") if base and sig(s) == base]
        if same:
            out.append(chk("WARN", f"indistinguishable_http_errors[{mode}]",
                           f"{', '.join(same)} surface exactly like http_500 {base}: a caller cannot tell "
                           "auth failure or rate limiting apart from a server crash"))
    return out


# --------------------------------------------------------------------------- report


LEVEL_ICON = {"FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️"}


def write_report(out_dir: Path, runs: list[dict[str, Any]], cross: list[dict[str, str]], meta: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps({"meta": meta, "cross_checks": cross, "runs": runs},
                                                     indent=2, default=str))
    L = [f"# MCP fault lab report", "",
         f"mcp {meta['mcp']} · opentelemetry-sdk {meta['otel']} · Python {meta['python']} · {meta['ran_at']}", "",
         "| scenario | mode | expect | got | time | recovered | flags |", "|---|---|---|---|---|---|---|"]
    for r in runs:
        flags = " ".join(f"{LEVEL_ICON[c['level']]}{c['check']}" for c in r["checks"]) or "✅"
        L.append(f"| {r['scenario']} | {r['mode']} | {r['expect']} | {r.get('outcome')} | {r.get('elapsed_s')}s "
                 f"| {'yes' if r.get('recovered') else 'NO'} | {flags} |")
    L += ["", "## Findings", ""]
    items = [(c, None) for c in cross] + [(c, r) for r in runs for c in r["checks"] if c["level"] != "INFO"]
    if not items:
        L.append("Nothing beyond INFO. The SDK handled every scenario the way the catalog expects.")
    for c, r in items:
        where = f" ({r['scenario']}, mode={r['mode']})" if r else ""
        L.append(f"- **{c['level']} {c['check']}**{where}: {c['detail']}")
        if r:
            L.append(f"  - repro: `python -m fault_lab.run --only {r['scenario']} --modes {r['mode']}`")
    L += ["", "## Before filing anything", "",
          "1. Re-run against SDK `main` (`pip install -e path/to/python-sdk`) to make sure it is not already fixed.",
          "2. Search open AND closed issues/PRs for the symptom, not just the title.",
          "3. Reduce to a standalone repro (no proxy if possible) and include versions.",
          "4. Propose a fix shape and offer to PR it; do not open a PR before a maintainer agrees on the shape.", ""]
    (out_dir / "report.md").write_text("\n".join(L))


# --------------------------------------------------------------------------- main


async def amain(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=out_dir / "harness.log", filemode="w", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logs = LogCollector()
    logging.getLogger().addHandler(logs)
    setup_tracing(args.otlp)

    up_port, px_port = free_port(), free_port()
    mcp_server = build_server()
    up = await serve(mcp_server.streamable_http_app(), up_port, "on")
    proxy = ChaosProxy(f"http://127.0.0.1:{up_port}")
    px = await serve(proxy, px_port, "off")
    url = f"http://127.0.0.1:{px_port}/mcp"

    chosen = [s for s in SCENARIOS if not args.only or s.id in args.only]
    if args.only and len(chosen) != len(args.only):
        known = {s.id for s in SCENARIOS}
        print(f"unknown scenario(s): {sorted(set(args.only) - known)}", file=sys.stderr)
        return 2

    runs = []
    try:
        for mode in args.modes:
            for sc in chosen:
                r = await run_scenario(sc, mode, url, proxy, logs, args.hang_budget)
                worst = max((c["level"] for c in r["checks"]), key=["INFO", "WARN", "FAIL"].index, default="ok")
                print(f"{mode:7} {sc.id:26} expect={sc.expect:10} got={r.get('outcome'):10} "
                      f"{r.get('elapsed_s')}s  {worst}", flush=True)
                runs.append(r)
    finally:
        for s in (px, up):
            s.should_exit = True
        await proxy.aclose()
        await asyncio.sleep(0.3)

    cross = cross_checks(runs)
    meta = {"mcp": version("mcp"), "otel": version("opentelemetry-sdk"),
            "python": sys.version.split()[0], "ran_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "modes": args.modes, "hang_budget_s": args.hang_budget,
            "scenarios": [asdict(s) for s in chosen]}
    write_report(out_dir, runs, cross, meta)
    fails = sum(c["level"] == "FAIL" for r in runs for c in r["checks"])
    warns = sum(c["level"] == "WARN" for r in runs for c in r["checks"]) + len(cross)
    print(f"\n{len(runs)} runs, {fails} FAIL, {warns} WARN -> {out_dir / 'report.md'}")
    return 1 if fails else 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="*", help="scenario ids to run (default: all)")
    p.add_argument("--modes", nargs="*", default=["auto", "legacy"],
                   help="Client protocol modes: auto (modern, 2026-07-28 when available) and/or legacy")
    p.add_argument("--hang-budget", type=float, default=6.0, help="seconds before a call counts as hung")
    p.add_argument("--out", default="out", help="output directory")
    p.add_argument("--otlp", default=None, help="OTLP/HTTP endpoint, e.g. http://localhost:4318")
    sys.exit(asyncio.run(amain(p.parse_args())))


if __name__ == "__main__":
    main()
