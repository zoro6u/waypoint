"""
analyze_pilot.py — summarize logs/pilot_log.jsonl into the comparison
tables from the original pilot design: pass rate and latency broken
down by difficulty, by task_type, and by source (mbpp vs handwritten —
the familiarity/generalization check).

Usage:
    python analyze_pilot.py
    python analyze_pilot.py --log logs/pilot_log_manual_sanity_check.jsonl
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load(path):
    return [json.loads(line) for line in Path(path).read_text().strip().splitlines()]


def summarize(records, group_key):
    groups = defaultdict(lambda: defaultdict(list))
    for r in records:
        if "runner_error" in r:
            continue
        groups[r.get(group_key)][r.get("model")].append(r)

    rows = []
    for key, by_model in groups.items():
        row = {group_key: key}
        for model in ("cheap", "premium"):
            recs = by_model.get(model, [])
            if not recs:
                row[f"{model}_pass_rate"] = None
                row[f"{model}_latency_ms"] = None
                continue
            row[f"{model}_pass_rate"] = round(sum(r["pass_rate"] for r in recs) / len(recs), 3)
            row[f"{model}_latency_ms"] = round(sum(r["latency_ms"] for r in recs) / len(recs))
        rows.append(row)
    return rows


def print_table(rows, group_key, title):
    print(f"\n=== {title} ===")
    print(f"{group_key:<20} {'cheap pass':>12} {'cheap ms':>10} {'premium pass':>14} {'premium ms':>12}")
    for row in sorted(rows, key=lambda r: str(r[group_key])):
        print(
            f"{str(row[group_key]):<20} {str(row.get('cheap_pass_rate')):>12} "
            f"{str(row.get('cheap_latency_ms')):>10} {str(row.get('premium_pass_rate')):>14} "
            f"{str(row.get('premium_latency_ms')):>12}"
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="logs/pilot_log.jsonl")
    args = ap.parse_args()

    records = load(args.log)

    errors = [r for r in records if "runner_error" in r]
    if errors:
        print(f"WARNING: {len(errors)} runner_error entries excluded from analysis:")
        for e in errors:
            print(f"  {e.get('problem_id')} x {e.get('model')}: {e.get('runner_error')}")

    print_table(summarize(records, "difficulty"), "difficulty", "By difficulty")
    print_table(summarize(records, "task_type"), "task_type", "By task_type")
    print_table(summarize(records, "source"), "source", "By source (mbpp vs handwritten)")

    extraction_fails = [r for r in records if r.get("extraction_failed")]
    execution_fails = [r for r in records if r.get("execution_failed")]
    print(f"\nextraction_failed: {len(extraction_fails)} / {len(records)}")
    print(f"execution_failed:  {len(execution_fails)} / {len(records)}")

    cheap = [r for r in records if r.get("model") == "cheap" and "pass_rate" in r]
    premium = [r for r in records if r.get("model") == "premium" and "pass_rate" in r]
    if cheap and premium:
        overall_cheap = sum(r["pass_rate"] for r in cheap) / len(cheap)
        overall_premium = sum(r["pass_rate"] for r in premium) / len(premium)
        gap = overall_premium - overall_cheap
        print(f"\noverall pass rate — cheap: {overall_cheap:.3f}  premium: {overall_premium:.3f}  gap: {gap:+.3f}")
        if gap <= 0.02:
            print("NOTE: near-zero quality gap on this pilot set at the pass/fail level.")


if __name__ == "__main__":
    main()
