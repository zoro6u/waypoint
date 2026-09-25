"""
Synthetic-input test of sandbox.py's NEW status taxonomy, per CLAUDE.md
rule 7. Monkeypatches subprocess.run so no real Podman container is
involved — we are testing the CLASSIFIER, not the container.
"""
import subprocess
import sys
import time
import types

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import sandbox

def fake_proc(stdout, stderr=""):
    p = types.SimpleNamespace()
    p.stdout, p.stderr, p.returncode = stdout, stderr, 0
    return p

def patch(stdout, stderr="", delay=0.0, raise_timeout=False):
    def _run(cmd, **kw):
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))
        if delay:
            time.sleep(delay)
        return fake_proc(stdout, stderr)
    sandbox.subprocess.run = _run

def hdr(n):
    return ("============================= test session starts ===========\n"
            f"collecting ... collected {n} items\n\n")

results, failures = [], []
def check(name, got, want):
    ok = got == want
    results.append((name, ok, got, want))
    if not ok:
        failures.append(name)

# --- 1. PASS: every test passes -------------------------------------
out = hdr(3) + "".join(f"test_solution.py::test_{i} PASSED   [ 33%]\n" for i in range(3))
patch(out)
r = sandbox.run_in_sandbox("x=1", ["assert 1", "assert 1", "assert 1"], timeout_s=10)
check("PASS status", r["status"], "PASS")
check("PASS passed-flag", r["passed"], True)
check("PASS pass_rate", r["pass_rate"], 1.0)
check("PASS failed_tests", r["failed_tests"], [])

# --- 2. VALUE_MISMATCH: all ran, every failure AssertionError -------
out = (hdr(3)
       + "test_solution.py::test_0 PASSED\n"
       + "test_solution.py::test_1 FAILED\n"
       + "test_solution.py::test_2 FAILED\n\n"
       + "=========================== short test summary info =========\n"
       + "FAILED test_solution.py::test_1 - AssertionError: assert {'A': 1} == {'A': 2}\n"
       + "FAILED test_solution.py::test_2 - AssertionError: assert [] == [1]\n")
patch(out)
r = sandbox.run_in_sandbox("x=1", ["a", "b", "c"], timeout_s=10)
check("VALUE_MISMATCH status", r["status"], "VALUE_MISMATCH")
check("VALUE_MISMATCH passed-flag", r["passed"], False)
check("VALUE_MISMATCH tests_passed", r["tests_passed"], 1)
# THE int/str KEY-MISMATCH REGRESSION GUARD: exception types must resolve,
# never silently fall back to "Unknown"
check("VALUE_MISMATCH exc types resolved",
      [f["exception_type"] for f in r["failed_tests"]],
      ["AssertionError", "AssertionError"])
check("VALUE_MISMATCH indices are ints",
      [f["index"] for f in r["failed_tests"]], [1, 2])

# --- 3. RUNTIME_ERROR: all ran, one non-AssertionError failure ------
out = (hdr(3)
       + "test_solution.py::test_0 PASSED\n"
       + "test_solution.py::test_1 FAILED\n"
       + "test_solution.py::test_2 FAILED\n\n"
       + "=========================== short test summary info =========\n"
       + "FAILED test_solution.py::test_1 - KeyError: 'Widget'\n"
       + "FAILED test_solution.py::test_2 - AssertionError: assert 1 == 2\n")
patch(out)
r = sandbox.run_in_sandbox("x=1", ["a", "b", "c"], timeout_s=10)
check("RUNTIME_ERROR status", r["status"], "RUNTIME_ERROR")
check("RUNTIME_ERROR exc types",
      sorted(f["exception_type"] for f in r["failed_tests"]),
      ["AssertionError", "KeyError"])

# --- 4. CRASH: zero result lines, returned FAST ---------------------
out = ("=========================== ERRORS ==========================\n"
       "ImportError while importing test module '/sandbox/test_solution.py'.\n"
       "E   NameError: name 'foo' is not defined\n")
patch(out)
r = sandbox.run_in_sandbox("foo(", ["a", "b"], timeout_s=10)
check("CRASH status", r["status"], "CRASH")
check("CRASH tests_passed", r["tests_passed"], 0)
check("CRASH pass_rate", r["pass_rate"], 0.0)

# --- 5. TIMEOUT (a): some tests ran, then output stopped ------------
out = (hdr(10)
       + "test_solution.py::test_0 PASSED\n"
       + "test_solution.py::test_1 PASSED\n"
       + "test_solution.py::test_2 \n")
patch(out)
r = sandbox.run_in_sandbox("x=1", ["a"]*10, timeout_s=10)
check("TIMEOUT-partial status", r["status"], "TIMEOUT")
check("TIMEOUT-partial keeps partial count", r["tests_passed"], 2)
check("TIMEOUT-partial tests_total", r["tests_total"], 10)

# --- 6. TIMEOUT (b): zero lines AND consumed the timeout budget -----
patch("(hung, nothing printed)", delay=0.9)
r = sandbox.run_in_sandbox("while True: pass", ["a", "b"], timeout_s=1)
check("TIMEOUT-hang status", r["status"], "TIMEOUT")

# --- 7. TIMEOUT: outer subprocess safety net fired -----------------
patch("", raise_timeout=True)
r = sandbox.run_in_sandbox("x=1", ["a", "b"], timeout_s=1)
check("TIMEOUT-outer status", r["status"], "TIMEOUT")
check("TIMEOUT-outer passed-flag", r["passed"], False)

# --- 8. debug field convention -------------------------------------
check("_raw_output present", "_raw_output" in r, True)

print(f"{'CASE':<38} {'RESULT':<6} detail")
for name, ok, got, want in results:
    print(f"{name:<38} {'ok' if ok else 'FAIL':<6} " + ("" if ok else f"got={got!r} want={want!r}"))
print(f"\n{len(results)-len(failures)}/{len(results)} checks passed")
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("status taxonomy verified against synthetic inputs")
