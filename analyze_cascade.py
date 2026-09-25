"""
analyze_cascade.py — summarize a cascade log (from run_cascade.py) into
the metrics that actually matter for Stage 1: cheap_pass_rate,
escalation_rate, premium_recovery_rate, final_pass_rate, latency, and
the failure_type distribution (with recovery rate broken down BY
failure type — this is the observational data the escalation-rule
question for Stage 2 should be answered from, not assumed).

Usage:
    python analyze_cascade.py --log logs/cascade_stress_log.jsonl
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load(path):
    return [json.loads(line) for line in Path(path).read_text().strip().splitlines()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    args = ap.parse_args()

    records = load(args.log)
    errors = [r for r in records if "runner_error" in r]
    rows = [r for r in records if "runner_error" not in r]

    if errors:
        print(f"WARNING: {len(errors)} runner_error rows excluded from analysis:")
        for e in errors:
            print(f"  {e.get('problem_id')}: {e.get('runner_error')}")

    n = len(rows)
    if n == 0:
        print("no usable rows")
        return

    cheap_passed = sum(1 for r in rows if r["cheap_passed"])
    escalated = [r for r in rows if r["escalated"]]
    final_passed = sum(1 for r in rows if r["final_passed"])
    recovered = sum(1 for r in escalated if r["premium_passed"])

    print(f"=== Stage 1 cascade summary (n={n}) ===")
    print(f"cheap_pass_rate:      {cheap_passed}/{n} = {cheap_passed/n:.3f}")
    print(f"escalation_rate:      {len(escalated)}/{n} = {len(escalated)/n:.3f}")
    if escalated:
        print(f"premium_recovery_rate (of escalated): {recovered}/{len(escalated)} = {recovered/len(escalated):.3f}")
    else:
        print("premium_recovery_rate: n/a (no escalations)")
    print(f"final_pass_rate:      {final_passed}/{n} = {final_passed/n:.3f}")

    avg_cheap_latency = sum(r["cheap_latency_ms"] for r in rows) / n
    avg_total_latency = sum(r["total_latency_ms"] for r in rows) / n
    avg_total_latency_no_esc = (
        sum(r["total_latency_ms"] for r in rows if not r["escalated"])
        / max(1, n - len(escalated))
    )
    avg_total_latency_esc = (
        sum(r["total_latency_ms"] for r in escalated) / len(escalated) if escalated else 0
    )
    print(f"\navg cheap_latency_ms (all rows):        {avg_cheap_latency:.0f}")
    print(f"avg total_latency_ms (no escalation):    {avg_total_latency_no_esc:.0f}")
    print(f"avg total_latency_ms (escalated rows):    {avg_total_latency_esc:.0f}")
    print(f"avg total_latency_ms (overall):          {avg_total_latency:.0f}")

    # failure_type distribution, with recovery rate broken down BY
    # cheap's failure type — this is the data Stage 2's escalation
    # rule question should be answered from, not assumed in advance
    by_status = defaultdict(list)
    for r in rows:
        by_status[r["cheap_status"]].append(r)

    print("\n=== cheap_status distribution & recovery-by-type ===")
    print(f"{'cheap_status':<18} {'count':>6} {'share':>7}   {'escalated?':<11} {'premium_recovery':>17}")
    for status, group in sorted(by_status.items(), key=lambda kv: -len(kv[1])):
        count = len(group)
        share = count / n
        is_esc = "yes" if status != "PASS" else "no"
        if status == "PASS":
            recovery_str = "n/a"
        else:
            recov = sum(1 for r in group if r["premium_passed"])
            recovery_str = f"{recov}/{count} = {recov/count:.2f}"
        print(f"{status:<18} {count:>6} {share:>6.1%}   {is_esc:<11} {recovery_str:>17}")


if __name__ == "__main__":
    main()
