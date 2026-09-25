"""
sandbox.py — run extracted code against a problem's tests inside an
isolated, resource-limited Podman container.

Isolation layers (defense in depth — no single one is trusted alone):
  1. --network=none            no network access at all
  2. --memory / --cpus         resource caps
  3. --pids-limit              fork-bomb protection
  4. --read-only + tmpfs /tmp  no writes to the container's own rootfs
  5. `timeout Ns` as the container's actual command   hard wall-clock cutoff
  6. an outer subprocess timeout                       safety net if podman
                                                         itself hangs

NOTE: this module is untested in the sandbox this code was authored in
(no Podman available there). It has NOT been run end-to-end yet — that
has to happen on your machine. Treat the first real run as a test of
this file, not as ground truth data.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

IMAGE = "waypoint-sandbox:latest"
CONTAINER_MEMORY = "256m"
CONTAINER_CPUS = "1"
PIDS_LIMIT = "64"

# pytest -v output line, e.g.:
#   test_solution.py::test_0 PASSED
#   test_solution.py::test_1 FAILED
PASS_LINE_RE = re.compile(r"^test_solution\.py::test_(\d+)\s+(PASSED|FAILED|ERROR)", re.MULTILINE)


def _build_test_file(tests: list) -> str:
    """One test_N() per assertion, so a single wrong assert doesn't
    take down the whole test's other assertions with it."""
    lines = ["from solution import *", ""]
    for i, t in enumerate(tests):
        lines.append(f"def test_{i}():")
        lines.append(f"    {t}")
        lines.append("")
    return "\n".join(lines)


def run_in_sandbox(code: str, tests: list, timeout_s: int = 10) -> dict:
    """
    Returns:
      {
        "execution_failed": bool,  # couldn't get a clean per-test result
                                    # at all: import crash, container/
                                    # timeout failure, podman error
        "tests_passed": int,
        "tests_total": int,
        "pass_rate": float,
        "_raw_output": str,        # first 4000 chars, for debugging only —
                                    # strip this before writing to the log
      }
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

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 5)
        except subprocess.TimeoutExpired:
            return {
                "execution_failed": True,
                "tests_passed": 0,
                "tests_total": tests_total,
                "pass_rate": 0.0,
                "_raw_output": "outer subprocess timeout — podman did not exit in time",
            }

        output = proc.stdout + "\n" + proc.stderr
        matches = PASS_LINE_RE.findall(output)

        if len(matches) != tests_total:
            # collection error (e.g. import crash in solution.py), a
            # podman/image problem, or unexpected output shape
            return {
                "execution_failed": True,
                "tests_passed": 0,
                "tests_total": tests_total,
                "pass_rate": 0.0,
                "_raw_output": output[:4000],
            }

        passed = sum(1 for _, status in matches if status == "PASSED")
        return {
            "execution_failed": False,
            "tests_passed": passed,
            "tests_total": tests_total,
            "pass_rate": round(passed / tests_total, 4) if tests_total else 0.0,
            "_raw_output": output[:4000],
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
