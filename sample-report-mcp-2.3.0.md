# MCP fault lab report

mcp 2.3.0 · opentelemetry-sdk 1.45.1 · Python 3.13.16 · 2026-10-20 21:59:39 EST

| scenario | mode | expect | got | time | recovered | flags |
|---|---|---|---|---|---|---|
| baseline_ok | auto | ok | ok | 0.01s | yes | ✅ |
| tool_anticipated_failure | auto | tool_error | tool_error | 0.01s | yes | ✅ |
| tool_crash | auto | tool_error | tool_error | 0.01s | yes | ✅ |
| tool_timeout | auto | timeout | timeout | 1.5s | yes | ✅ |
| http_401 | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_403 | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_404 | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_429 | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_500 | auto | error | error | 0.01s | yes | ⚠️http_status_lost |
| http_502 | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_503 | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_500_jsonrpc_body | auto | error | error | 0.0s | yes | ⚠️http_status_lost |
| stall_no_timeout | auto | hang_info | hang | 6.0s | yes | ℹ️caller_hangs ⚠️client_span_not_error |
| stall_with_timeout | auto | timeout | timeout | 1.5s | yes | ✅ |
| slow_within_timeout | auto | ok | ok | 1.01s | yes | ✅ |
| truncate_sse | auto | error | error | 0.0s | yes | ✅ |
| truncate_json | auto | error | client_crashed | 0.0s | NO | ❌transport_error_kills_client |
| garbage_json | auto | error | error | 0.0s | yes | ✅ |
| html_200 | auto | error | error | 0.0s | yes | ✅ |
| empty_200 | auto | error | error | 0.0s | yes | ✅ |
| baseline_ok | legacy | ok | ok | 0.01s | yes | ✅ |
| tool_anticipated_failure | legacy | tool_error | tool_error | 0.01s | yes | ✅ |
| tool_crash | legacy | tool_error | tool_error | 0.01s | yes | ✅ |
| tool_timeout | legacy | timeout | timeout | 1.5s | yes | ✅ |
| http_401 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_403 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_404 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_429 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_500 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_502 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_503 | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| http_500_jsonrpc_body | legacy | error | error | 0.0s | yes | ⚠️http_status_lost |
| stall_no_timeout | legacy | hang_info | hang | 6.0s | yes | ℹ️caller_hangs ⚠️client_span_not_error |
| stall_with_timeout | legacy | timeout | timeout | 1.5s | yes | ✅ |
| slow_within_timeout | legacy | ok | ok | 1.01s | yes | ✅ |
| truncate_sse | legacy | error | error | 0.0s | yes | ✅ |
| truncate_json | legacy | error | client_crashed | 0.0s | NO | ❌transport_error_kills_client |
| garbage_json | legacy | error | error | 0.0s | yes | ✅ |
| html_200 | legacy | error | error | 0.0s | yes | ✅ |
| empty_200 | legacy | error | error | 0.0s | yes | ✅ |

## Findings

- **WARN indistinguishable_http_errors[auto]**: http_401, http_403, http_429, http_502, http_503 surface exactly like http_500 ('mcp.shared.exceptions.MCPError', -32603, 'Server returned an error response'): a caller cannot tell auth failure or rate limiting apart from a server crash
- **WARN indistinguishable_http_errors[legacy]**: http_401, http_403, http_429, http_502, http_503 surface exactly like http_500 ('mcp.shared.exceptions.MCPError', -32603, 'Server returned an error response'): a caller cannot tell auth failure or rate limiting apart from a server crash
- **WARN http_status_lost** (http_401, mode=auto): caller sees code=-32603 message='Server returned an error response'; HTTP 401 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_401 --modes auto`
- **WARN http_status_lost** (http_403, mode=auto): caller sees code=-32603 message='Server returned an error response'; HTTP 403 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_403 --modes auto`
- **WARN http_status_lost** (http_404, mode=auto): caller sees code=-32601 message='Not Found'; HTTP 404 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_404 --modes auto`
- **WARN http_status_lost** (http_429, mode=auto): caller sees code=-32603 message='Server returned an error response'; HTTP 429 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_429 --modes auto`
- **WARN http_status_lost** (http_500, mode=auto): caller sees code=-32603 message='Server returned an error response'; HTTP 500 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_500 --modes auto`
- **WARN http_status_lost** (http_502, mode=auto): caller sees code=-32603 message='Server returned an error response'; HTTP 502 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_502 --modes auto`
- **WARN http_status_lost** (http_503, mode=auto): caller sees code=-32603 message='Server returned an error response'; HTTP 503 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_503 --modes auto`
- **WARN http_status_lost** (http_500_jsonrpc_body, mode=auto): caller sees code=-32000 message='synthetic upstream overload'; HTTP 500 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_500_jsonrpc_body --modes auto`
- **WARN client_span_not_error** (stall_no_timeout, mode=auto): client span status is UNSET although the call failed (hang)
  - repro: `python -m fault_lab.run --only stall_no_timeout --modes auto`
- **FAIL transport_error_kills_client** (truncate_json, mode=auto): a fault on ONE request escaped `async with Client` as httpx2.RemoteProtocolError: peer closed connection without sending complete message body (received 22 bytes, expected 200) (the call itself ended as 'client_crashed'); the whole session is gone instead of that call failing
  - repro: `python -m fault_lab.run --only truncate_json --modes auto`
- **WARN http_status_lost** (http_401, mode=legacy): caller sees code=-32603 message='Server returned an error response'; HTTP 401 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_401 --modes legacy`
- **WARN http_status_lost** (http_403, mode=legacy): caller sees code=-32603 message='Server returned an error response'; HTTP 403 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_403 --modes legacy`
- **WARN http_status_lost** (http_404, mode=legacy): caller sees code=-32600 message='Session terminated'; HTTP 404 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_404 --modes legacy`
- **WARN http_status_lost** (http_429, mode=legacy): caller sees code=-32603 message='Server returned an error response'; HTTP 429 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_429 --modes legacy`
- **WARN http_status_lost** (http_500, mode=legacy): caller sees code=-32603 message='Server returned an error response'; HTTP 500 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_500 --modes legacy`
- **WARN http_status_lost** (http_502, mode=legacy): caller sees code=-32603 message='Server returned an error response'; HTTP 502 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_502 --modes legacy`
- **WARN http_status_lost** (http_503, mode=legacy): caller sees code=-32603 message='Server returned an error response'; HTTP 503 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_503 --modes legacy`
- **WARN http_status_lost** (http_500_jsonrpc_body, mode=legacy): caller sees code=-32000 message='synthetic upstream overload'; HTTP 500 is not in the exception message or data
  - repro: `python -m fault_lab.run --only http_500_jsonrpc_body --modes legacy`
- **WARN client_span_not_error** (stall_no_timeout, mode=legacy): client span status is UNSET although the call failed (hang)
  - repro: `python -m fault_lab.run --only stall_no_timeout --modes legacy`
- **FAIL transport_error_kills_client** (truncate_json, mode=legacy): a fault on ONE request escaped `async with Client` as httpx2.RemoteProtocolError: peer closed connection without sending complete message body (received 22 bytes, expected 200) (the call itself ended as 'client_crashed'); the whole session is gone instead of that call failing
  - repro: `python -m fault_lab.run --only truncate_json --modes legacy`

## Before filing anything

1. Re-run against SDK `main` (`pip install -e path/to/python-sdk`) to make sure it is not already fixed.
2. Search open AND closed issues/PRs for the symptom, not just the title.
3. Reduce to a standalone repro (no proxy if possible) and include versions.
4. Propose a fix shape and offer to PR it; do not open a PR before a maintainer agrees on the shape.
