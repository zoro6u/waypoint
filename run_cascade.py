"""
run_cascade.py — sweep every problem in a dataset through the Stage 1
cascade router (cascade.run_cascade), logging one flat record per
problem.

Each run is wrapped in its own try/except, same convention as
run_pilot.py: a single crashed call doesn't abort the whole sweep.

--log has no default on purpose — this is a different log SHAPE
(flat cascade records) than run_pilot.py's per-(problem,model) records,
so an accidental shared default risked mixing two incompatible schemas
in one file.

Usage:
    python run_cascade.py --dataset stress_dataset.json --log logs/cascade_stress_log.jsonl
    python run_cascade.py --dataset pilot_dataset.json --log logs/cascade_pilot_log.jsonl
"""

import argparse
import json
import time
from pathlib import Path

from cascade import run_cascade
from runner import DATASET_PATH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cheap-model", default="qwen2.5-coder:1.5b")
    ap.add_argument("--premium-model", default="qwen2.5-coder:7b")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--dataset", default=str(DATASET_PATH), help="path to the problem set JSON")
    ap.add_argument("--log", required=True, help="path to append JSON cascade log lines to")
    args = ap.parse_args()

    dataset_path = Path(args.dataset)
    log_path = Path(args.log)
    problems = json.loads(dataset_path.read_text())

    log_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(problems)
    sweep_start = time.monotonic()

    for i, p in enumerate(problems, 1):
        print(f"[{i}/{total}] {p['id']} ...", end=" ", flush=True)
        run_start = time.monotonic()
        try:
            record = run_cascade(
                p["id"], args.cheap_model, args.premium_model,
                timeout_s=args.timeout, dataset_path=dataset_path,
            )
        except Exception as e:  # noqa: BLE001 — sweep must not die on one bad run
            record = {"problem_id": p["id"], "runner_error": f"{type(e).__name__}: {e}"}
            print(f"RUNNER_ERROR: {e}")
        else:
            elapsed = time.monotonic() - run_start
            esc = "ESCALATED" if record["escalated"] else "no-escalate"
            print(
                f"cheap={record['cheap_status']} {esc} "
                f"final={record['final_status']} [{elapsed:.1f}s]"
            )

        # keep debug fields (already prefixed _cheap_.../ _premium_...
        # by cascade.py) whenever escalation happened — that's exactly
        # when we want to see WHY cheap failed and what premium actually
        # did, REGARDLESS of whether premium went on to recover it. The
        # premium_recovery_rate question needs cheap's failure detail on
        # every escalated row, not just the ones that stayed broken.
        keep_debug = record.get("escalated") is True or "runner_error" in record
        if keep_debug:
            clean_record = {(k.lstrip("_") if k.startswith("_") else k): v for k, v in record.items()}
        else:
            clean_record = {k: v for k, v in record.items() if not k.startswith("_")}

        with log_path.open("a") as f:
            f.write(json.dumps(clean_record, ensure_ascii=False) + "\n")

    total_elapsed = time.monotonic() - sweep_start
    print(f"\nDone: {total} problems in {total_elapsed/60:.1f} min. Log: {log_path}")


if __name__ == "__main__":
    main()
