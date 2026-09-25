"""
runner.py — CLI entrypoint. Runs ONE problem against ONE model and
appends one JSON log line.

Supports multiple datasets (the pilot_dataset.json 12 and the separate
stress_dataset.json set) via --dataset / --log, so pilot and stress
results never mix in the same log file unless explicitly pointed there.

Usage:
    python runner.py --problem wp_001 --model qwen2.5-coder:1.5b --tag cheap
    python runner.py --problem stress_001 --model qwen2.5-coder:1.5b --tag cheap \\
        --dataset stress_dataset.json --log logs/stress_log.jsonl
"""

import argparse
import json
from pathlib import Path

from extraction import extract_python_code
from ollama_client import call_ollama
from sandbox import run_in_sandbox

BASE_DIR = Path(__file__).parent
DATASET_PATH = BASE_DIR / "pilot_dataset.json"  # default; overridable via --dataset
LOG_PATH = BASE_DIR / "logs" / "pilot_log.jsonl"  # default; overridable via --log

PROMPT_TEMPLATE = """{prompt}

Return ONLY the function implementation as a single Python code block, \
starting with `def {function_name}(`. Do not include usage examples or \
explanation outside the code block."""


def load_problem(problem_id: str, dataset_path: Path = None) -> dict:
    dataset_path = dataset_path or DATASET_PATH
    problems = json.loads(Path(dataset_path).read_text())
    for p in problems:
        if p["id"] == problem_id:
            return p
    raise ValueError(f"unknown problem id: {problem_id} (looked in {dataset_path})")


def run_one(problem_id: str, model: str, tag: str, timeout_s: int = 10, dataset_path: Path = None) -> dict:
    problem = load_problem(problem_id, dataset_path)
    prompt = PROMPT_TEMPLATE.format(prompt=problem["prompt"], function_name=problem["function_name"])

    gen = call_ollama(model=model, prompt=prompt, temperature=0.0)
    extraction = extract_python_code(gen["response"], problem["function_name"])

    record = {
        "problem_id": problem["id"],
        "source": problem["source"],
        "difficulty": problem["difficulty"],
        "task_type": problem["task_type"],
        "model": tag,
        "model_name": model,
        "temperature": 0.0,
        "latency_ms": gen["latency_ms"],
        "extraction_failed": not extraction["success"],
        "extraction_failure_reason": extraction["reason"],
    }

    if not extraction["success"]:
        record.update({
            "execution_failed": False,
            "tests_passed": 0,
            "tests_total": len(problem["tests"]),
            "pass_rate": 0.0,
        })
        record["_raw_response"] = gen["response"][:2000]  # debug only, stripped before logging
    else:
        result = run_in_sandbox(extraction["code"], problem["tests"], timeout_s=timeout_s)
        record.update(result)  # includes "_raw_output", debug only
        # keep the actual extracted code as a debug field too — knowing
        # WHICH tests failed is useless without being able to read WHY
        record["_extracted_code"] = extraction["code"]

    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", required=True, help="problem id, e.g. wp_001")
    ap.add_argument("--model", required=True, help="Ollama model tag, e.g. qwen2.5-coder:1.5b")
    ap.add_argument("--tag", required=True, choices=["cheap", "premium"], help="router tier this model represents")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--dataset", default=str(DATASET_PATH), help="path to the problem set JSON")
    ap.add_argument("--log", default=str(LOG_PATH), help="path to append the JSON log line to")
    args = ap.parse_args()

    record = run_one(args.problem, args.model, args.tag, args.timeout, dataset_path=Path(args.dataset))

    # console: full record, debug fields included
    print(json.dumps(record, indent=2, ensure_ascii=False))

    # persisted log: keep debug fields (extracted code, raw output) only
    # when the run ISN'T a clean pass — that's exactly when they're needed
    # to diagnose why, and stripping them for the common full-pass case
    # keeps the log lean.
    is_clean_pass = not record.get("extraction_failed") and not record.get("execution_failed") and record.get("pass_rate") == 1.0
    if is_clean_pass:
        clean_record = {k: v for k, v in record.items() if not k.startswith("_")}
    else:
        clean_record = {(k.lstrip("_") if k.startswith("_") else k): v for k, v in record.items()}
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(clean_record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
