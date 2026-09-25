"""
cascade.py — Stage 1 router: minimal cheap -> premium cascade.

    cheap
      |
      +-- PASS -----------> return result
      |
      +-- anything else --> premium
                                |
                                +-- PASS -----> return result
                                +-- failure --> return failure (still)

The router looks at exactly ONE field to decide whether to escalate:
cheap_result["passed"]. Nothing else about the cheap attempt (its
status, which tests failed, which exception types) feeds the
escalation decision in Stage 1 — that detail is recorded as telemetry
only, per the explicit decision not to assume which failure types are
worth escalating before real Stage 1 data can answer that question.

Record shape is deliberately FLAT (not nested cheap/premium sub-dicts)
so later analysis (groupby cheap_status, etc.) doesn't need to unpack
nested JSON.
"""

from runner import run_one


def run_cascade(problem_id: str, cheap_model: str, premium_model: str,
                 timeout_s: int = 10, dataset_path=None) -> dict:
    cheap = run_one(problem_id, cheap_model, "cheap", timeout_s=timeout_s, dataset_path=dataset_path)

    record = {
        "problem_id": cheap["problem_id"],
        "source": cheap["source"],
        "difficulty": cheap["difficulty"],
        "task_type": cheap["task_type"],
        "cheap_model": cheap_model,
        "premium_model": premium_model,
        "cheap_status": cheap["status"],
        "cheap_passed": cheap["passed"],
        "cheap_pass_rate": cheap["pass_rate"],
        "cheap_latency_ms": cheap["latency_ms"],
        "escalated": not cheap["passed"],
    }
    # keep the cheap attempt's own debug fields (raw response/output,
    # extracted code) only when it wasn't a clean pass — same
    # lean-log-except-when-needed convention as runner.py/run_pilot.py
    if not cheap["passed"]:
        for k, v in cheap.items():
            if k.startswith("_"):
                record[f"_cheap{k}"] = v

    if cheap["passed"]:
        record.update({
            "premium_status": None,
            "premium_passed": None,
            "premium_pass_rate": None,
            "premium_latency_ms": None,
            "final_model": "cheap",
            "final_status": cheap["status"],
            "final_passed": True,
            "total_latency_ms": cheap["latency_ms"],
        })
        return record

    premium = run_one(problem_id, premium_model, "premium", timeout_s=timeout_s, dataset_path=dataset_path)
    record.update({
        "premium_status": premium["status"],
        "premium_passed": premium["passed"],
        "premium_pass_rate": premium["pass_rate"],
        "premium_latency_ms": premium["latency_ms"],
        "final_model": "premium",
        "final_status": premium["status"],
        "final_passed": premium["passed"],
        "total_latency_ms": cheap["latency_ms"] + premium["latency_ms"],
    })
    if not premium["passed"]:
        for k, v in premium.items():
            if k.startswith("_"):
                record[f"_premium{k}"] = v
    return record
