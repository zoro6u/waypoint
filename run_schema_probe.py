"""
run_schema_probe.py — drive the experimental schema-repair shadow path over
an existing cascade log, and report actual outcomes against the prediction
recorded below.

Reads `premium_extracted_code` out of a cascade log (no new model calls — the
generations are fixed), derives each problem's contract from the frozen
tests, wraps the model's function so extra keys are deleted, and re-runs the
SAME frozen tests through the unchanged `run_in_sandbox`.

PREDICTION ON RECORD (written before the first real run, so that the
comparison is honest rather than reconstructed afterwards):
  stress_001  plausibly RECOVERED  — the only defect visible in its output is
              the extra bookkeeping key `top_product_revenue`. Hedged because
              its top_product tie-break compares per-record revenue against
              top_product_revenue rather than accumulated per-product
              revenue, so it may still fail the multi-record tie cases on
              values alone.
  stress_002  STILL_FAILED — extras (`total_sum`, `latest_timestamp`) AND the
              contract key `latest` is absent. Delete-only cannot invent it.
  stress_004  UNSUPPORTED_SHAPE — returns a bare list, nothing to prune. This
              is the no-op control: a validator that correctly does nothing
              is part of the verification.
  Headline expectation: 0-1 recoveries out of 3, NOT 2.

Usage:
    python run_schema_probe.py --cascade-log logs/cascade_stress_log.jsonl \\
        --dataset stress_dataset.json --log logs/schema_probe_log.jsonl
"""

import argparse
import json
from pathlib import Path

from sandbox import run_in_sandbox
from schema_probe import build_wrapper, derive_contract, prune

PREDICTION = {
    "stress_001": "RECOVERED (hedged)",
    "stress_002": "STILL_FAILED",
    "stress_004": "UNSUPPORTED_SHAPE",
}


def probe_one(row: dict, problem: dict, timeout_s: int = 10) -> dict:
    pid = problem["id"]
    fn = problem["function_name"]
    out = {
        "problem_id": pid,
        "function_name": fn,
        "baseline_premium_status": row.get("premium_status"),
        "baseline_premium_pass_rate": row.get("premium_pass_rate"),
        "baseline_premium_passed": row.get("premium_passed"),
        "predicted_verdict": PREDICTION.get(pid),
    }

    model_code = row.get("premium_extracted_code")
    if not model_code:
        out["repair_verdict"] = "NO_CODE_IN_LOG"
        out["note"] = ("premium passed on this problem, so its extracted code "
                       "was stripped from the log as a clean pass")
        return out

    derived = derive_contract(problem["tests"], fn)
    out["derive_verdict"] = derived["verdict"]
    out["skipped_tests"] = derived.get("skipped_tests")
    if derived["verdict"] != "DERIVED":
        out["repair_verdict"] = derived["verdict"]
        out["derive_reason"] = derived.get("reason")
        return out

    contract = derived["contract_keys"]
    out["contract_keys"] = sorted(contract)
    out["outer_classified_as_data"] = derived["outer_varies"]
    out["outer_key_sets"] = derived["outer_key_sets"]

    wrapper = build_wrapper(model_code, fn, contract)
    res = run_in_sandbox(wrapper, problem["tests"], timeout_s=timeout_s)

    out.update({
        "repaired_status": res["status"],
        "repaired_passed": res["passed"],
        "repaired_pass_rate": res["pass_rate"],
        "repaired_failed_tests": res["failed_tests"],
        "_repaired_raw_output": res["_raw_output"],
        "_wrapper_code": wrapper,
    })
    if res["passed"]:
        out["repair_verdict"] = "RECOVERED"
    else:
        out["repair_verdict"] = "STILL_FAILED"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cascade-log", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--timeout", type=int, default=10)
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.cascade_log).read_text().strip().splitlines()]
    problems = {p["id"]: p for p in json.loads(Path(args.dataset).read_text())}

    escalated = [r for r in rows if "runner_error" not in r and r.get("escalated")]
    failed = [r for r in escalated if r.get("premium_passed") is False]

    results = []
    for row in failed:
        pid = row["problem_id"]
        print(f"probing {pid} ...", end=" ", flush=True)
        res = probe_one(row, problems[pid], timeout_s=args.timeout)
        print(f"{res['repair_verdict']}  (predicted: {res['predicted_verdict']})")
        results.append(res)

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as f:
        for res in results:
            # same convention as elsewhere: keep debug fields only when the
            # outcome needs them — a RECOVERED row must stay auditable
            keep = res.get("repair_verdict") != "NO_CODE_IN_LOG"
            rec = ({(k.lstrip("_") if k.startswith("_") else k): v for k, v in res.items()}
                   if keep else {k: v for k, v in res.items() if not k.startswith("_")})
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- metrics: experimental, reported alongside the frozen numbers ----
    n_rows = len([r for r in rows if "runner_error" not in r])
    n_failed = len(failed)
    recovered = [r for r in results if r["repair_verdict"] == "RECOVERED"]
    frozen_final_pass = sum(1 for r in rows
                            if "runner_error" not in r and r.get("final_passed"))

    print(f"\n=== schema repair (EXPERIMENTAL — does not replace frozen metrics) ===")
    print(f"premium failures examined (F):        {n_failed}")
    print(f"schema_repair_recovery_rate:          {len(recovered)}/{n_failed} = "
          f"{len(recovered)/n_failed:.3f}" if n_failed else "n/a")
    print(f"final_pass_rate (FROZEN, unchanged):  {frozen_final_pass}/{n_rows} = "
          f"{frozen_final_pass/n_rows:.3f}")
    print(f"final_pass_rate_if_repaired (EXPT):   "
          f"{frozen_final_pass + len(recovered)}/{n_rows} = "
          f"{(frozen_final_pass + len(recovered))/n_rows:.3f}")
    print(f"schema_repair_delta:                  {len(recovered)}/{n_rows} = "
          f"{len(recovered)/n_rows:.3f}")
    print("\nNOTE: output shape is part of the specified behavior, so a repaired")
    print("pass measures a deliberately weaker benchmark — semantic correctness")
    print("given shape forgiveness. Never quote this as premium's pass rate.")

    print(f"\n=== predicted vs actual ===")
    print(f"{'problem':<12} {'predicted':<22} {'actual':<20} {'match?'}")
    for res in results:
        pred = res["predicted_verdict"] or "(none)"
        act = res["repair_verdict"]
        match = "yes" if pred.split()[0] == act else "NO"
        print(f"{res['problem_id']:<12} {pred:<22} {act:<20} {match}")

    for res in results:
        if res.get("deleted_keys") or res.get("repair_verdict") == "RECOVERED":
            print(f"\n{res['problem_id']} audit: contract={res.get('contract_keys')}")
    print(f"\nLog: {log_path}")


if __name__ == "__main__":
    main()
