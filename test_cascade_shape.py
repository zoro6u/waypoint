"""Dry-run of cascade.run_cascade with runner.run_one stubbed: verifies the
flat record shape, the escalation branch, and the debug-field convention
without touching Ollama or Podman."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import cascade

def rec(tag, status, passed, pass_rate, latency, failed_tests=None):
    return {"problem_id": "t_001", "source": "stress", "difficulty": "stress",
            "task_type": "composite", "model": tag, "model_name": f"m-{tag}",
            "status": status, "passed": passed, "pass_rate": pass_rate,
            "latency_ms": latency, "tests_passed": 0, "tests_total": 7,
            "failed_tests": failed_tests if failed_tests is not None else [],
            "_raw_response": f"{tag} raw",
            "_extracted_code": f"{tag} code", "_raw_output": f"{tag} out"}

fails, checks = [], []
def check(n, got, want):
    checks.append((n, got == want, got, want))
    if got != want: fails.append(n)

# --- cheap PASSES -> no escalation, no premium call -----------------
calls = []
def stub_pass(pid, model, tag, timeout_s=10, dataset_path=None):
    calls.append(tag)
    return rec(tag, "PASS", True, 1.0, 20000)
cascade.run_one = stub_pass
r = cascade.run_cascade("t_001", "cheap-m", "prem-m")
check("no-esc: premium never called", calls, ["cheap"])
check("no-esc: escalated", r["escalated"], False)
check("no-esc: final_model", r["final_model"], "cheap")
check("no-esc: final_passed", r["final_passed"], True)
check("no-esc: premium_status None", r["premium_status"], None)
check("no-esc: total_latency", r["total_latency_ms"], 20000)
check("no-esc: no debug fields leaked", [k for k in r if k.startswith("_")], [])
check("no-esc: cheap_failed_tests recorded as []", r["cheap_failed_tests"], [])
check("no-esc: premium_failed_tests None", r["premium_failed_tests"], None)

# --- cheap FAILS, premium RECOVERS ---------------------------------
calls = []
def stub_recover(pid, model, tag, timeout_s=10, dataset_path=None):
    calls.append(tag)
    if tag == "cheap":
        return rec(tag, "VALUE_MISMATCH", False, 0.42, 30000)
    return rec(tag, "PASS", True, 1.0, 110000)
cascade.run_one = stub_recover
r = cascade.run_cascade("t_001", "cheap-m", "prem-m")
check("recover: both called", calls, ["cheap", "premium"])
check("recover: escalated", r["escalated"], True)
check("recover: cheap_status", r["cheap_status"], "VALUE_MISMATCH")
check("recover: premium_passed", r["premium_passed"], True)
check("recover: final_passed", r["final_passed"], True)
check("recover: final_model", r["final_model"], "premium")
check("recover: total_latency summed", r["total_latency_ms"], 140000)
# cheap debug kept (it failed); premium debug NOT kept (it passed)
check("recover: cheap debug kept", "_cheap_raw_response" in r, True)
check("recover: premium debug absent", any(k.startswith("_premium") for k in r), False)

# --- cheap FAILS, premium ALSO FAILS -------------------------------
def stub_bothfail(pid, model, tag, timeout_s=10, dataset_path=None):
    if tag == "cheap":
        return rec(tag, "RUNTIME_ERROR", False, 0.14, 25000)
    return rec(tag, "VALUE_MISMATCH", False, 0.25, 100000)
cascade.run_one = stub_bothfail
r = cascade.run_cascade("t_001", "cheap-m", "prem-m")
check("bothfail: final_passed", r["final_passed"], False)
check("bothfail: final_status", r["final_status"], "VALUE_MISMATCH")
check("bothfail: cheap debug kept", "_cheap_raw_response" in r, True)
check("bothfail: premium debug kept", "_premium_raw_response" in r, True)

# --- EXTRACTION_FAILED on cheap still escalates (routes on `passed` only)
def stub_extract(pid, model, tag, timeout_s=10, dataset_path=None):
    if tag == "cheap":
        return rec(tag, "EXTRACTION_FAILED", False, 0.0, 15000)
    return rec(tag, "PASS", True, 1.0, 90000)
cascade.run_one = stub_extract
r = cascade.run_cascade("t_001", "cheap-m", "prem-m")
check("extract-fail escalates", r["escalated"], True)
check("extract-fail recovered", r["final_passed"], True)

# --- failed_tests PROPAGATION: the field cascade.py used to drop -----
CHEAP_FT = [{"index": 1, "exception_type": "KeyError"},
            {"index": 2, "exception_type": "AssertionError"}]
PREM_FT = [{"index": 3, "exception_type": "AssertionError"}]
def stub_ft(pid, model, tag, timeout_s=10, dataset_path=None):
    if tag == "cheap":
        return rec(tag, "RUNTIME_ERROR", False, 0.42, 30000, failed_tests=CHEAP_FT)
    return rec(tag, "VALUE_MISMATCH", False, 0.25, 110000, failed_tests=PREM_FT)
cascade.run_one = stub_ft
r = cascade.run_cascade("t_001", "cheap-m", "prem-m")
check("ft: cheap_failed_tests survives router", r["cheap_failed_tests"], CHEAP_FT)
check("ft: premium_failed_tests survives router", r["premium_failed_tests"], PREM_FT)
check("ft: cheap exception types readable without re-parsing text",
      [f["exception_type"] for f in r["cheap_failed_tests"]],
      ["KeyError", "AssertionError"])
# must survive the log round-trip too, since these are NOT _-prefixed
# debug fields and so must not be stripped on either branch
import json as _json
check("ft: survives json round-trip",
      _json.loads(_json.dumps(r))["cheap_failed_tests"], CHEAP_FT)
check("ft: not treated as a debug field",
      any(k.startswith("_") and "failed_tests" in k for k in r), False)

print(f"{'CASE':<40} RESULT")
for n, ok, got, want in checks:
    print(f"{n:<40} {'ok' if ok else 'FAIL'}" + ("" if ok else f"  got={got!r} want={want!r}"))
print(f"\n{len(checks)-len(fails)}/{len(checks)} checks passed")
sys.exit(1 if fails else 0)
