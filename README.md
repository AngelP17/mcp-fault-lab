# mcp-fault-lab

Runs the MCP Python SDK client through real network failures with OpenTelemetry tracing on, and reports what breaks. Everything is synthetic: a toy MCP server, a fault-injecting proxy, no real data.

```
client (mcp.Client) ──> chaos proxy (fault_lab/chaos_proxy.py) ──> MCPServer (fault_lab/server.py)
          └── OTel: MCP client span ─> httpx2 span        server span (linked via _meta traceparent)
```

The proxy only faults `tools/call`, so the handshake and the recovery call in each scenario stay clean. Each scenario:

1. connects, warms up with `echo`
2. turns the fault on, makes the target call under a hang budget
3. turns the fault off, calls `echo` again on the same session (did it recover?)
4. closes the client (did teardown hang?)
5. checks the outcome against what a well-behaved client should do, plus the spans

## Run

```bash
pip install -r requirements.txt
python -m fault_lab.run                                  # all scenarios, auto + legacy protocol modes
python -m fault_lab.run --only truncate_json --modes auto
python -m fault_lab.run --otlp http://localhost:4318     # also send traces to Jaeger / Tempo / Grafana
python repros/transport_error_kills_client.py            # minimal standalone repro, no proxy
```

Output goes to `out/`: `report.md` (table + findings), `report.json` (every exception, span, and log line), `harness.log`.

Exit code is 1 if any FAIL check fired, so it can run in CI against new SDK releases.

## Scenarios

See `fault_lab/scenarios.py`. HTTP 401/403/404/429/500/502/503, JSON-RPC error bodies on 500, stalls with and without timeouts, slow upstream, connection drops mid SSE and mid JSON, HTML or empty bodies, tool crashes and timeouts. Add a `Scenario(...)` line to add a case; new fault kinds go in `ChaosProxy._apply`.

## Results on mcp 2.3.0 (2026-10-20)

| finding | level | status upstream |
|---|---|---|
| A transport error on one POST (connection drop mid JSON body, or server gone) escapes `async with Client` as an ExceptionGroup and kills the session | FAIL | Already reported as [python-sdk #3522](https://github.com/modelcontextprotocol/python-sdk/issues/3522) and [#3560](https://github.com/modelcontextprotocol/python-sdk/issues/3560); additional variants and repro posted on #3522. Same code on `main` @ 2af9e30. |
| Non-2xx statuses other than 404 all become `-32603 "Server returned an error response"`; 401, 403, 429, 502, 503 are indistinguishable from 500 and the HTTP status is not in the error | WARN | Known; tracked around python-sdk #2110 / #1742 (typed errors). The httpx2 OTel span does carry the real status, so traces show it even though callers can't. |
| No default request timeout: a stalled upstream blocks `call_tool` forever unless `read_timeout_seconds` is set; the cancelled call's client span ends with status UNSET | INFO | Not checked upstream yet. Probably by design for the timeout; the UNSET span is worth asking about. |
| Gateway 404 mid-call: auto mode reports `-32601 Not Found`, legacy reports `-32600 Session terminated` | INFO | Matches the code comments; mention only if it bites someone. |

Handled well: truncated SSE, HTML or empty JSON bodies, JSON-RPC error bodies on 500, timeouts, tool crashes (no internal detail leaked), session recovery after every non-transport fault.

## Rules

- Synthetic servers and data only. Never point this at employer systems or paste employer code into a repro.
- Before filing: re-run on `main`, search open and closed issues for the symptom, reduce to a standalone repro.
- If it's already reported, add evidence to the existing issue instead of opening a duplicate.

## Regression CI

The baseline profile pins MCP 2.3.0; the latest profile installs the current compatible SDK. Transitive dependencies are recorded with `pip freeze` in each CI artifact, not claimed to be fully locked. Each run publishes raw reports and a generated `matrix.md`. Known session-killing transport failures in `truncate_json` are classified XFAIL; new failures fail CI, missing fault application is ERROR, and fixed known failures are XPASS. Historical October findings are not statements about current SDK behavior. See [claim-to-proof matrix](docs/CLAIMS.md).
