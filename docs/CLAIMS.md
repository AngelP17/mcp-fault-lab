# Claim-to-proof matrix

| Claim | Evidence |
|---|---|
| HTTP faults, stalls, dropped connections and malformed payloads | fault_lab/scenarios.py and chaos_proxy.py |
| Same-session recovery and teardown observations | fault_lab/run.py; generated report.json |
| Session-killing transport defect | repros/transport_error_kills_client.py; [upstream issue](https://github.com/modelcontextprotocol/python-sdk/issues/3522) |
| Trace capture | generated report.json spans |
| Versioned regression classification | scripts/classify_report.py; tests/test_classify_report.py |

CI artifacts record dependencies, SDK version and commit. Baseline pins the SDK but not transitive dependencies. No current regression outcome is claimed until CI runs; historical results remain dated. All data and servers are synthetic.
