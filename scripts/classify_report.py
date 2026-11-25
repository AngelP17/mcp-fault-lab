"""Classify complete lab output without hiding unknown failures."""
import argparse
import json
from pathlib import Path

KNOWN = {("truncate_json", "transport_error_kills_client"): "https://github.com/modelcontextprotocol/python-sdk/issues/3522"}


def classify(run):
    failures = {c["check"] for c in run["checks"] if c["level"] == "FAIL"}
    if "fault_not_applied" in failures or not run.get("outcome"):
        return "ERROR"
    expected = {check for scenario, check in KNOWN if scenario == run["scenario"]}
    if failures - expected:
        return "FAIL"
    if failures:
        return "XFAIL"
    if expected:
        return "XPASS"
    return "PASS"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("report", type=Path)
    p.add_argument("--runner-exit", type=int, required=True)
    p.add_argument("--expected-runs", type=int, default=40)
    a = p.parse_args()
    data = json.loads(a.report.read_text())
    runs = data["runs"]
    identities = {(r["scenario"], r["mode"]) for r in runs}
    if len(runs) != a.expected_runs or len(identities) != len(runs) or a.runner_exit not in (0, 1):
        raise SystemExit("ERROR: incomplete or invalid harness execution")
    rows = [{**r, "verdict": classify(r)} for r in runs]
    data["classified_runs"] = [{"scenario": r["scenario"], "mode": r["mode"], "verdict": r["verdict"]} for r in rows]
    a.report.with_name("classified.json").write_text(json.dumps(data, indent=2) + "\n")
    lines = ["# Regression matrix", "", f"SDK: {data['meta']['mcp']} | Commit: {data['meta'].get('commit')}", "",
             "| Scenario | Mode | Expected | Observed | Recovered | Verdict | Issue |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        issue = next((url for (s, _), url in KNOWN.items() if s == r["scenario"]), "")
        lines.append(f"| {r['scenario']} | {r['mode']} | {r['expect']} | {r.get('outcome')} | {r.get('recovered')} | {r['verdict']} | {issue} |")
    a.report.with_name("matrix.md").write_text("\n".join(lines) + "\n")
    return int(any(r["verdict"] in {"FAIL", "ERROR"} for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
