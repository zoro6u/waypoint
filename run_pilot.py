"""
run_pilot.py — sweep every problem in pilot_dataset.json against both
the cheap and premium model, logging every result.

Reuses runner.run_one() directly rather than shelling out per-problem,
so this is one continuous process — no repeated Python/import startup
cost per run.

Each run is wrapped in its own try/except: a single crashed Ollama
call or an unexpected exception does NOT abort the sweep. It's logged
as a runner_error and the sweep moves on to the next run. For a 24-run
unattended sweep, losing everything after run 13 because of one
transient failure would be worse than a slightly messier log.

Usage:
    python run_pilot.py
    python run_pilot.py --cheap-model qwen2.5-coder:1.5b --premium-model qwen2.5-coder:7b
"""

import argparse
import json
import re
import time
from pathlib import Path

from runner import DATASET_PATH, LOG_PATH, run_one

# same shape as sandbox.PASS_LINE_RE — reused here just to summarize
# per-test outcomes for console/log visibility on non-clean-pass runs
TEST_LINE_RE = re.compile(r"^test_solution\.py::test_(\d+)\s+(PASSED|FAILED|ERROR)", re.MULTILINE)


def _per_test_summary(raw_output: str) -> str:
    """e.g. 'test_0=PASSED test_1=FAILED test_2=PASSED ...' — cheap way
    to see WHICH tests failed without re-running anything."""
    matches = TEST_LINE_RE.findall(raw_output or "")
    if not matches:
        return "(no per-test output captured)"
    return " ".join(f"test_{i}={status}" for i, status in matches)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cheap-model", default="qwen2.5-coder:1.5b")
    ap.add_argument("--premium-model", default="qwen2.5-coder:7b")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--dataset", default=str(DATASET_PATH), help="path to the problem set JSON")
    ap.add_argument("--log", default=str(LOG_PATH), help="path to append JSON log lines to")
    args = ap.parse_args()

    dataset_path = Path(args.dataset)
    log_path = Path(args.log)

    problems = json.loads(dataset_path.read_text())
    runs = [(p["id"], args.cheap_model, "cheap") for p in problems]
    runs += [(p["id"], args.premium_model, "premium") for p in problems]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(runs)
    sweep_start = time.monotonic()

    for i, (pid, model, tag) in enumerate(runs, 1):
        print(f"[{i}/{total}] {pid} x {tag} ({model}) ...", end=" ", flush=True)
        run_start = time.monotonic()
        try:
            record = run_one(pid, model, tag, timeout_s=args.timeout, dataset_path=dataset_path)
        except Exception as e:  # noqa: BLE001 — intentionally broad: sweep must not die
            record = {
                "problem_id": pid,
                "model": tag,
                "model_name": model,
                "runner_error": f"{type(e).__name__}: {e}",
            }
            print(f"RUNNER_ERROR: {e}")
        else:
            elapsed = time.monotonic() - run_start
            status = record.get("status", "UNKNOWN")
            print(f"{status} (pass_rate={record.get('pass_rate')}) [{elapsed:.1f}s]")
            if status not in ("PASS", "EXTRACTION_FAILED"):
                print(f"    {_per_test_summary(record.get('_raw_output', ''))}")

        # keep debug fields in the persisted log for anything that ISN'T a
        # clean pass — that's exactly when we need them to diagnose why.
        # Strip them (as before) only for full passes, to keep the common
        # case's log lean.
        if record.get("status") == "PASS" and "runner_error" not in record:
            clean_record = {k: v for k, v in record.items() if not k.startswith("_")}
        else:
            clean_record = {
                (k.lstrip("_") if k.startswith("_") else k): v for k, v in record.items()
            }
        with log_path.open("a") as f:
            f.write(json.dumps(clean_record, ensure_ascii=False) + "\n")

    total_elapsed = time.monotonic() - sweep_start
    print(f"\nDone: {total} runs in {total_elapsed/60:.1f} min. Log: {log_path}")


if __name__ == "__main__":
    main()
