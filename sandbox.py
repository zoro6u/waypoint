"""
sandbox.py — run extracted code against a problem's tests inside an
isolated, resource-limited Podman container, and return a STRUCTURED
result: a `status` from a fixed taxonomy, decided here from pytest's
own output — not reconstructed later from raw text by a downstream
classifier. Evaluator -> Observation -> Router stays a clean pipeline;
nothing downstream has to re-parse text to know what happened.

Isolation layers (defense in depth — no single one is trusted alone):
  1. --network=none            no network access at all
  2. --memory / --cpus         resource caps
  3. --pids-limit              fork-bomb protection
  4. --read-only + tmpfs /tmp  no writes to the container's own rootfs
  5. `timeout Ns` as the container's actual command   hard wall-clock cutoff
  6. an outer subprocess timeout                       safety net if podman
                                                         itself hangs

Status taxonomy (priority order when multiple signals are present —
first match wins):
  CRASH          solution.py never produced a single per-test result at
                 all, AND returned well before the timeout budget was
                 used up (an import-time exception, not a hang).
  TIMEOUT        either (a) some tests produced results and then output
                 stopped (killed mid-run), or (b) zero results AND the
                 process consumed close to the full timeout budget
                 (hung with nothing ever printed) — these two are
                 distinguished by elapsed time as an approximation, not
                 a certainty.
  RUNTIME_ERROR  every test ran to completion, and at least one FAILED
                 test's exception was something other than
                 AssertionError (KeyError, TypeError, ZeroDivisionError,
                 ...) — treated as evidence of an execution-time
                 failure, not assumed to reveal the underlying semantic
                 cause.
  VALUE_MISMATCH every test ran to completion, and every failure was a
                 plain AssertionError (code ran, answer was wrong).
  PASS           every test passed.

(EXTRACTION_FAILED is not decided here — that happens in runner.py
before this module is ever called, since it's a pre-execution failure
this module has no visibility into.)

NOTE: this module is exercised on real Podman/Ollama runs as of the
stress_001-004 experiments, so the pass/fail counting and output
parsing are proven. The status-taxonomy layer above that (CRASH vs
TIMEOUT elapsed-time heuristic, and the exception-type parsing) is
NEW and has NOT been run against a real CRASH case yet — treat the
first CRASH/TIMEOUT split you see as a test of this logic.
"""

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

IMAGE = "waypoint-sandbox:latest"
CONTAINER_MEMORY = "256m"
CONTAINER_CPUS = "1"
PIDS_LIMIT = "64"

# pytest -v output line, e.g.:
#   test_solution.py::test_0 PASSED
#   test_solution.py::test_1 FAILED
PASS_LINE_RE = re.compile(r"^test_solution\.py::test_(\d+)\s+(PASSED|FAILED|ERROR)", re.MULTILINE)

# pytest's "short test summary info" line for a failed test, e.g.:
#   FAILED test_solution.py::test_2 - KeyError: 'Widget'
#   FAILED test_solution.py::test_1 - AssertionError: assert {'Tools': ...
EXCEPTION_LINE_RE = re.compile(r"^FAILED test_solution\.py::test_(\d+) - (\w+):", re.MULTILINE)

# if a test genuinely hung until killed, treat "used >= this fraction of
# the timeout budget" as evidence of a hang rather than a fast crash
TIMEOUT_FRACTION_THRESHOLD = 0.8


def _build_test_file(tests: list) -> str:
    """One test_N() per assertion, so a single wrong assert doesn't
    take down the whole test's other assertions with it."""
    lines = ["from solution import *", ""]
    for i, t in enumerate(tests):
        lines.append(f"def test_{i}():")
        lines.append(f"    {t}")
        lines.append("")
    return "\n".join(lines)


def _make_result(status: str, tests_passed: int, tests_total: int, raw_output: str,
                  failed_tests: list = None) -> dict:
    return {
        "status": status,
        "passed": status == "PASS",
        "tests_passed": tests_passed,
        "tests_total": tests_total,
        "pass_rate": round(tests_passed / tests_total, 4) if tests_total else 0.0,
        "failed_tests": failed_tests or [],
        "_raw_output": raw_output[:4000],
    }


def run_in_sandbox(code: str, tests: list, timeout_s: int = 10) -> dict:
    """
    Returns a dict with, at minimum: status, passed, tests_passed,
    tests_total, pass_rate, failed_tests (list of {"index", "exception_type"}
    for RUNTIME_ERROR/VALUE_MISMATCH statuses), and "_raw_output" (debug
    only — strip before persisting to a log, same convention as elsewhere).
    """
    tests_total = len(tests)
    tmpdir = Path(tempfile.mkdtemp(prefix="waypoint_"))
    try:
        (tmpdir / "solution.py").write_text(code)
        (tmpdir / "test_solution.py").write_text(_build_test_file(tests))

        cmd = [
            "podman", "run", "--rm",
            "--network=none",
            f"--memory={CONTAINER_MEMORY}",
            f"--cpus={CONTAINER_CPUS}",
            f"--pids-limit={PIDS_LIMIT}",
            "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{tmpdir}:/sandbox:ro,Z",
            "--workdir", "/sandbox",
            IMAGE,
            "timeout", f"{timeout_s}s",
            "python", "-m", "pytest", "-v", "--tb=no", "-p", "no:cacheprovider",
            "test_solution.py",
        ]

        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 5)
            elapsed = time.monotonic() - start
        except subprocess.TimeoutExpired:
            # outer safety net fired — podman itself didn't exit in time
            return _make_result("TIMEOUT", 0, tests_total,
                                 "outer subprocess timeout — podman did not exit in time")

        output = proc.stdout + "\n" + proc.stderr
        matches = PASS_LINE_RE.findall(output)

        if len(matches) == 0:
            # nothing ran at all: either it crashed immediately (import
            # error) or it hung the whole time and got killed with zero
            # output. Elapsed time is the only signal we have to tell
            # these apart, so it's an approximation, not a certainty.
            if elapsed >= timeout_s * TIMEOUT_FRACTION_THRESHOLD:
                return _make_result("TIMEOUT", 0, tests_total, output)
            return _make_result("CRASH", 0, tests_total, output)

        if len(matches) < tests_total:
            # some tests completed, then output stopped — killed mid-run
            passed_so_far = sum(1 for _, status in matches if status == "PASSED")
            return _make_result("TIMEOUT", passed_so_far, tests_total, output)

        # every test produced a result line — classify by exception type
        # for the ones that failed
        exception_by_index = {int(i): exc for i, exc in EXCEPTION_LINE_RE.findall(output)}
        passed = sum(1 for _, status in matches if status == "PASSED")

        if passed == tests_total:
            return _make_result("PASS", passed, tests_total, output)

        failed_tests = [
            {"index": int(i), "exception_type": exception_by_index.get(int(i), "Unknown")}
            for i, status in matches if status != "PASSED"
        ]
        non_assertion = [f for f in failed_tests if f["exception_type"] != "AssertionError"]
        status = "RUNTIME_ERROR" if non_assertion else "VALUE_MISMATCH"
        return _make_result(status, passed, tests_total, output, failed_tests)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
