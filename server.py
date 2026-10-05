"""
server.py — thin local HTTP gateway over the Stage 1 cascade.

    POST /route
    {"prompt": "...", "function_name": "...", "tests": ["assert f(1) == 2", ...]}
    -> {"final_model", "escalated", "passed", "final_status", "cheap_status",
        "premium_status", "code", "total_latency_ms"}

The cascade judges quality with hidden tests; here the CALLER supplies
those tests. Routing is unchanged: cascade.run_cascade_problem escalates
on `passed` only (rule 5). Models, temperature/seed and timeout_s=10 are
fixed server-side (rules 1-2) — the caller cannot set them.

Deliberately minimal (see README "Security limits"):
- binds 127.0.0.1 only, hard-coded;
- one request runs at a time (CPU-bound Ollama serializes anyway); a
  request arriving while one is running gets 503 immediately;
- nothing is persisted — logs/ is experimental data and stays untouched.

Usage:
    python server.py [--port 8765]
"""

import argparse
import ast
import json
import keyword
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from cascade import run_cascade_problem

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CHEAP_MODEL = "qwen2.5-coder:1.5b"     # same defaults as run_cascade.py
PREMIUM_MODEL = "qwen2.5-coder:7b"
TIMEOUT_S = 10                          # rule 2: fixed, never per-request

MAX_BODY_BYTES = 64 * 1024
RECV_TIMEOUT_S = 10                     # per socket recv, NOT per request
MAX_PROMPT_CHARS = 4000
MAX_TESTS = 20
MAX_TEST_CHARS = 500
FIELDS = {"prompt", "function_name", "tests"}


def _check_test(t, function_name: str):
    """Reason string if test `t` is unusable, else None.

    sandbox._build_test_file writes each test as ONE indented line inside
    its own `def test_N():`. Anything that isn't a single assert statement
    on one line breaks the whole test file — which the sandbox would then
    report as a CRASH and blame on the model, so it's rejected up front."""
    if not isinstance(t, str):
        return f"must be a string, got {type(t).__name__}"
    if len(t) > MAX_TEST_CHARS:
        return f"longer than {MAX_TEST_CHARS} characters"
    if any(ord(c) < 32 or ord(c) == 127 for c in t) or len(t.splitlines()) != 1:
        return "must be a single line (no newline or other control characters)"
    if not t.startswith("assert"):
        return "must start with 'assert' (no leading whitespace)"
    try:
        tree = ast.parse(t)
    except SyntaxError as e:
        return f"not valid Python: {e.msg}"
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assert):
        return "must be exactly one assert statement"
    if not any(isinstance(n, ast.Name) and n.id == function_name for n in ast.walk(tree)):
        return f"does not reference function_name {function_name!r}"
    return None


def validate_request(body):
    """Returns (problem, None) for a usable request, else (None, reason).
    Pure — no I/O — so it's unit-testable on its own."""
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    missing, extra = FIELDS - body.keys(), body.keys() - FIELDS
    if missing:
        return None, f"missing field(s): {', '.join(sorted(missing))}"
    if extra:
        return None, (f"unexpected field(s): {', '.join(sorted(extra))} "
                      "(models and timeout are fixed server-side)")

    prompt, function_name, tests = body["prompt"], body["function_name"], body["tests"]
    if not isinstance(prompt, str) or not prompt.strip():
        return None, "prompt must be a non-empty string"
    if len(prompt) > MAX_PROMPT_CHARS:
        return None, f"prompt longer than {MAX_PROMPT_CHARS} characters"
    if not isinstance(function_name, str) or not function_name.isidentifier() \
            or keyword.iskeyword(function_name):
        return None, "function_name must be a valid Python identifier (not a keyword)"
    if not isinstance(tests, list) or not tests:
        return None, "tests must be a non-empty list of strings"
    if len(tests) > MAX_TESTS:
        return None, f"at most {MAX_TESTS} tests allowed"
    for i, t in enumerate(tests):
        reason = _check_test(t, function_name)
        if reason:
            return None, f"tests[{i}]: {reason}"

    # source/difficulty/task_type are read by cascade.py; "adhoc" keeps
    # these records distinguishable from dataset problems
    return {"id": "adhoc", "source": "adhoc", "difficulty": "adhoc", "task_type": "adhoc",
            "prompt": prompt, "function_name": function_name, "tests": list(tests)}, None


def route(problem: dict) -> dict:
    """Run the cascade and shape the response. `code` comes from the tier
    that produced the final verdict, and is returned whether or not that
    verdict is a pass (None only if extraction failed)."""
    tiers = {}
    record = run_cascade_problem(problem, CHEAP_MODEL, PREMIUM_MODEL, timeout_s=TIMEOUT_S,
                                 on_tier=lambda tag, r: tiers.__setitem__(tag, r))
    final = tiers.get(record["final_model"], {})
    return {
        "final_model": record["final_model"],
        "escalated": record["escalated"],
        "passed": record["final_passed"],
        "final_status": record["final_status"],
        "cheap_status": record["cheap_status"],
        "premium_status": record["premium_status"],
        "code": final.get("_extracted_code"),
        "cheap_latency_ms": record["cheap_latency_ms"],
        "premium_latency_ms": record["premium_latency_ms"],
        "total_latency_ms": record["total_latency_ms"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "waypoint"
    # StreamRequestHandler applies this to the socket: every single recv
    # (and send) must make progress within it. It bounds a stalled client,
    # not the total request time — the cascade itself does no socket I/O.
    timeout = RECV_TIMEOUT_S

    def _send(self, code: int, payload: dict, headers: dict = None):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if not getattr(self.server, "quiet", False):
            super().log_message(fmt, *args)

    def do_POST(self):
        if self.path != "/route":
            return self._send(404, {"error": "only POST /route exists"})

        # A web page open in a local browser can POST to 127.0.0.1 too.
        # Requiring application/json forces a CORS preflight (which this
        # server never answers), and the Host check defeats DNS rebinding.
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self._send(403, {"error": "Host header must be 127.0.0.1 or localhost"})
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._send(415, {"error": "Content-Type must be application/json"})

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return self._send(411, {"error": "Content-Length required (chunked bodies not supported)"})
        # int() alone accepts "-1", "+5", "1_0" and non-ASCII digits; a
        # negative length makes rfile.read() wait for EOF
        if not (raw_length.isascii() and raw_length.isdigit()):
            return self._send(400, {"error": "Content-Length must be a non-negative integer"})
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            return self._send(413, {"error": f"body larger than {MAX_BODY_BYTES} bytes"})
        try:
            raw_body = self.rfile.read(length)
        except TimeoutError:
            return self._send(408, {"error": f"body not received within {RECV_TIMEOUT_S}s "
                                             "(shorter than Content-Length?)"})
        try:
            body = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError) as e:
            return self._send(400, {"error": f"invalid JSON: {e}"})

        problem, reason = validate_request(body)
        if reason:
            return self._send(422, {"error": reason})

        lock = self.server.route_lock
        if not lock.acquire(blocking=False):
            return self._send(503, {"error": "busy: another request is running"},
                              {"Retry-After": "120"})
        # the response is sent only AFTER the lock is released, so a client
        # that retries the moment it reads a 502 never sees a stale 503
        try:
            status, payload = 200, route(problem)
        except requests.RequestException as e:
            status, payload = 502, {"error": f"Ollama call failed: {type(e).__name__}: {e}"}
        except Exception as e:  # noqa: BLE001 — report, don't drop the connection
            # full traceback to the operator's stderr; the client gets the
            # type only — the message can carry paths or other local detail
            traceback.print_exc()
            status, payload = 500, {"error": f"internal error: {type(e).__name__} (see server log)"}
        finally:
            lock.release()
        return self._send(status, payload)


def make_server(port: int = DEFAULT_PORT, quiet: bool = False) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((HOST, port), Handler)
    srv.route_lock = threading.Lock()
    srv.quiet = quiet
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    srv = make_server(args.port)
    print(f"waypoint gateway on http://{HOST}:{srv.server_address[1]}/route "
          f"(cheap={CHEAP_MODEL}, premium={PREMIUM_MODEL}, timeout_s={TIMEOUT_S})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
