"""Equivalence check for the run_one -> run_problem split (rule 7).

runner.run_one(problem_id) must return exactly what
runner.run_problem(load_problem(problem_id)) returns, and
cascade.run_cascade(problem_id) exactly what
cascade.run_cascade_problem(problem) returns, for every problem in both
datasets. Ollama and Podman are stubbed at the runner boundary; the real
extraction.py runs. No dataset or log file is written."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import cascade
import runner

BASE = Path(__file__).parent
DATASETS = [BASE / "pilot_dataset.json", BASE / "stress_dataset.json"]

fails, checks = [], []
def check(n, got, want):
    checks.append((n, got == want, got, want))
    if got != want: fails.append(n)

# --- stubs -----------------------------------------------------------
# mode decides per model what the "generation" looks like:
#   "pass"   -> fenced code that the sandbox stub passes
#   "fail"   -> fenced code that the sandbox stub fails
#   "nocode" -> prose only, so extraction fails
MODES = {}
sandbox_calls = []

def stub_ollama(model, prompt, temperature=0.0, seed=42, timeout_s=180):
    fn = prompt.split("starting with `def ")[1].split("(")[0]
    mode = MODES[model]
    if mode == "nocode":
        return {"response": "I cannot do that.", "latency_ms": 111}
    body = "return 1" if mode == "pass" else "return 0"
    return {"response": f"```python\ndef {fn}(*a, **k):\n    {body}\n```",
            "latency_ms": 222 if mode == "pass" else 333}

def stub_sandbox(code, tests, timeout_s=10):
    sandbox_calls.append(timeout_s)
    ok = "return 1" in code
    n = len(tests)
    return {"status": "PASS" if ok else "VALUE_MISMATCH", "passed": ok,
            "tests_passed": n if ok else 0, "tests_total": n,
            "pass_rate": 1.0 if ok else 0.0,
            "failed_tests": [] if ok else [{"index": 0, "exception_type": "AssertionError"}],
            "_raw_output": f"out {n}"}

runner.call_ollama = stub_ollama
runner.run_in_sandbox = stub_sandbox

# --- 1. run_one == run_problem, every problem, every outcome ---------
n_problems = 0
for ds in DATASETS:
    for pid in [p["id"] for p in __import__("json").loads(ds.read_text())]:
        n_problems += 1
        problem = runner.load_problem(pid, ds)
        for mode in ("pass", "fail", "nocode"):
            MODES["m"] = mode
            a = runner.run_one(pid, "m", "cheap", timeout_s=10, dataset_path=ds)
            b = runner.run_problem(problem, "m", "cheap", timeout_s=10)
            check(f"run_one==run_problem {pid} {mode}", a, b)
check("all 17 problems covered", n_problems, 17)

# --- 2. run_cascade == run_cascade_problem, every problem, 3 branches
SCENARIOS = {
    "cheap-pass": ("pass", "pass"),
    "recover":    ("fail", "pass"),
    "both-fail":  ("nocode", "fail"),
}
for ds in DATASETS:
    for pid in [p["id"] for p in __import__("json").loads(ds.read_text())]:
        problem = runner.load_problem(pid, ds)
        for name, (cm, pm) in SCENARIOS.items():
            MODES["c"], MODES["p"] = cm, pm
            a = cascade.run_cascade(pid, "c", "p", timeout_s=10, dataset_path=ds)
            seen = []
            b = cascade.run_cascade_problem(problem, "c", "p", timeout_s=10,
                                            on_tier=lambda t, r: seen.append((t, r)))
            check(f"cascade equal {pid} {name}", a, b)
            check(f"on_tier tiers {pid} {name}", [t for t, _ in seen],
                  ["cheap"] if name == "cheap-pass" else ["cheap", "premium"])

# --- 3. on_tier gets the full record even when the cascade drops it --
MODES["c"], MODES["p"] = "pass", "pass"
problem = runner.load_problem("wp_001", DATASETS[0])
seen = {}
r = cascade.run_cascade_problem(problem, "c", "p", on_tier=lambda t, rec: seen.update({t: rec}))
check("cheap-pass: cascade record has no debug fields", [k for k in r if k.startswith("_")], [])
check("cheap-pass: on_tier record carries extracted code",
      seen["cheap"].get("_extracted_code", "").startswith("def square_nums("), True)

# --- 4. ad-hoc problem dict flows through unchanged ------------------
adhoc = {"id": "adhoc", "source": "adhoc", "difficulty": "adhoc", "task_type": "adhoc",
         "prompt": "Write f.", "function_name": "f", "tests": ["assert f(1) == 1"]}
MODES["c"], MODES["p"] = "fail", "pass"
r = cascade.run_cascade_problem(adhoc, "c", "p")
check("adhoc: source/difficulty/task_type",
      (r["problem_id"], r["source"], r["difficulty"], r["task_type"]),
      ("adhoc", "adhoc", "adhoc", "adhoc"))
check("adhoc: escalated + recovered", (r["escalated"], r["final_model"], r["final_passed"]),
      (True, "premium", True))
check("timeout_s=10 reached the sandbox on every call", set(sandbox_calls), {10})

print(f"{'CASE':<48} RESULT")
for n, ok, got, want in checks:
    if not ok:
        print(f"{n:<48} FAIL  got={got!r} want={want!r}")
print(f"\n{len(checks)-len(fails)}/{len(checks)} checks passed")
sys.exit(1 if fails else 0)
