"""
demo_gateway.py — pre-registered predictions for the server.py live demo.

The two demo requests reuse a dataset problem's prompt and tests verbatim
(read from the dataset file, never retyped), so under temperature=0 /
seed=42 the gateway must reproduce the committed cascade outcome. The
predictions below were written from the committed logs BEFORE any request
was sent:

  wp_001     — logs/cascade_pilot_log.jsonl: cheap PASS, not escalated.
  stress_005 — logs/cascade_stress_log_v2.jsonl: cheap VALUE_MISMATCH,
               escalated, premium PASS. Secondary prediction: the returned
               code is byte-identical to the premium code recovered by the
               separate audit run (logs/stress_005_premium_audit_console.json).

Usage:
    python demo_gateway.py bodies OUTDIR          # write OUTDIR/<id>.json request bodies
    python demo_gateway.py check ID RESPONSE.json  # compare a response to the prediction
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
DATASET = {"wp_001": BASE / "pilot_dataset.json", "stress_005": BASE / "stress_dataset.json"}

PREDICTIONS = {
    "wp_001": {"final_model": "cheap", "escalated": False, "passed": True,
               "final_status": "PASS", "cheap_status": "PASS", "premium_status": None},
    "stress_005": {"final_model": "premium", "escalated": True, "passed": True,
                   "final_status": "PASS", "cheap_status": "VALUE_MISMATCH",
                   "premium_status": "PASS"},
}
CODE_PREDICTION = {"stress_005": BASE / "logs" / "stress_005_premium_audit_console.json"}


def body_for(pid: str) -> dict:
    p = next(p for p in json.loads(DATASET[pid].read_text()) if p["id"] == pid)
    return {"prompt": p["prompt"], "function_name": p["function_name"], "tests": p["tests"]}


def check(pid: str, response: dict) -> bool:
    ok = True
    for k, want in PREDICTIONS[pid].items():
        got = response.get(k)
        match = got == want
        ok &= match
        print(f"  {k:<16} predicted={want!r:<18} got={got!r:<18} {'ok' if match else 'MISMATCH'}")
    if pid in CODE_PREDICTION:
        want = json.loads(CODE_PREDICTION[pid].read_text())["_extracted_code"]
        match = response.get("code") == want
        ok &= match
        print(f"  {'code':<16} byte-identical to {CODE_PREDICTION[pid].name}: {'ok' if match else 'MISMATCH'}")
    print(f"{pid}: {'PREDICTION HELD' if ok else 'PREDICTION FAILED'}")
    return ok


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "bodies":
        out = Path(sys.argv[2])
        out.mkdir(parents=True, exist_ok=True)
        for pid in PREDICTIONS:
            (out / f"{pid}.json").write_text(json.dumps(body_for(pid), ensure_ascii=False))
            print(out / f"{pid}.json")
    elif len(sys.argv) == 4 and sys.argv[1] == "check":
        sys.exit(0 if check(sys.argv[2], json.loads(Path(sys.argv[3]).read_text())) else 1)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
