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

# --- 3b. completed run, summary block present but type unparseable ---
# must use the bare "Unknown" label, NOT the truncated one: a completed run
# that still yields no type is the signature of the old parsing bug
out = (hdr(2)
       + "test_solution.py::test_0 PASSED\n"
       + "test_solution.py::test_1 FAILED\n\n"
       + "=========================== short test summary info =========\n"
       + "FAILED test_solution.py::test_1 - !!!malformed!!!\n")
patch(out)
r = sandbox.run_in_sandbox("x=1", ["a", "b"], timeout_s=10)
check("completed-run: unparseable type keeps bare Unknown",
      [f["exception_type"] for f in r["failed_tests"]], ["Unknown"])
check("completed-run: does NOT use the truncated label",
      any(f["exception_type"] == "UNKNOWN_TRUNCATED" for f in r["failed_tests"]), False)

# --- 4. CRASH: zero result lines, returned FAST ---------------------
out = ("=========================== ERRORS ==========================\n"
       "ImportError while importing test module '/sandbox/test_solution.py'.\n"
       "E   NameError: name 'foo' is not defined\n")
patch(out)
r = sandbox.run_in_sandbox("foo(", ["a", "b"], timeout_s=10)
check("CRASH status", r["status"], "CRASH")
check("CRASH tests_passed", r["tests_passed"], 0)
check("CRASH pass_rate", r["pass_rate"], 0.0)
check("CRASH: no failed_tests (nothing ran)", r["failed_tests"], [])

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

# --- 5b. TIMEOUT partial: completed tests keep their verdicts --------
# reproduces the REAL stress_003 cheap shape: 9 result lines, test_9 hung,
# and no "short test summary info" block because pytest was killed
out = (hdr(10)
       + "test_solution.py::test_0 PASSED                    [ 10%]\n"
       + "".join(f"test_solution.py::test_{i} FAILED                    [ {i}0%]\n"
                for i in range(1, 8))
       + "test_solution.py::test_8 PASSED                    [ 90%]\n"
       + "test_solution.py::test_9 \n")
patch(out)
r = sandbox.run_in_sandbox("x=1", ["a"]*10, timeout_s=10)
check("TIMEOUT-partial: status", r["status"], "TIMEOUT")
check("TIMEOUT-partial: partial pass count", r["tests_passed"], 2)
check("TIMEOUT-partial: pass_rate", r["pass_rate"], 0.2)
check("TIMEOUT-partial: failed_tests no longer dropped",
      [f["index"] for f in r["failed_tests"]], [1, 2, 3, 4, 5, 6, 7])
check("TIMEOUT-partial: hung test NOT counted as failed",
      9 in [f["index"] for f in r["failed_tests"]], False)
check("TIMEOUT-partial: truncated label used, NOT bare Unknown",
      {f["exception_type"] for f in r["failed_tests"]}, {"UNKNOWN_TRUNCATED"})
# the two unknown-labels must stay distinct strings: the whole point of the
# rename is that telling them apart never depends on comment or test context
check("TIMEOUT-partial: truncated label != completed-run parse-failure label",
      sandbox.EXC_UNKNOWN_TRUNCATED != sandbox.EXC_UNKNOWN_PARSE, True)
check("TIMEOUT-partial: no bare 'Unknown' leaks onto a TIMEOUT row",
      any(f["exception_type"] == "Unknown" for f in r["failed_tests"]), False)

# a truncated run that DID emit a summary block keeps the real types
out2 = (hdr(5)
        + "test_solution.py::test_0 PASSED\n"
        + "test_solution.py::test_1 FAILED\n"
        + "test_solution.py::test_2 \n"
        + "=========================== short test summary info =========\n"
        + "FAILED test_solution.py::test_1 - KeyError: 'x'\n")
patch(out2)
r = sandbox.run_in_sandbox("x=1", ["a"]*5, timeout_s=10)
check("TIMEOUT-partial: real exc type used when available",
      [f["exception_type"] for f in r["failed_tests"]], ["KeyError"])

# --- 6. TIMEOUT (b): zero lines AND consumed the timeout budget -----
patch("(hung, nothing printed)", delay=0.9)
r = sandbox.run_in_sandbox("while True: pass", ["a", "b"], timeout_s=1)
check("TIMEOUT-hang status", r["status"], "TIMEOUT")
check("TIMEOUT-hang: no failed_tests (nothing ran)", r["failed_tests"], [])

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
