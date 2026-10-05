"""server.py checks (rule 7): the pure validator, the response shaping,
and the real HTTP layer on an ephemeral 127.0.0.1 port — with
cascade.run_cascade_problem stubbed, so no Ollama or Podman is touched."""
import io
import json
import socket
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import requests
import server

BASE = Path(__file__).parent
fails, checks = [], []
def check(n, got, want):
    checks.append((n, got == want, got, want))
    if got != want: fails.append(n)

def body(**over):
    b = {"prompt": "Write f.", "function_name": "f", "tests": ["assert f(1) == 2"]}
    b.update(over)
    return b

def reason(b):
    return server.validate_request(b)[1]

# --- 1. validator: every dataset problem must be accepted ------------
n = 0
for ds in ("pilot_dataset.json", "stress_dataset.json"):
    for p in json.loads((BASE / ds).read_text()):
        n += 1
        check(f"dataset accepted: {p['id']}",
              reason({"prompt": p["prompt"], "function_name": p["function_name"], "tests": p["tests"]}),
              None)
check("dataset problems checked", n, 17)

# --- 2. validator: accepted request -> ad-hoc problem dict -----------
prob, r = server.validate_request(body())
check("valid: no reason", r, None)
check("valid: adhoc fields", (prob["id"], prob["source"], prob["difficulty"], prob["task_type"]),
      ("adhoc", "adhoc", "adhoc", "adhoc"))
check("valid: payload carried", (prob["prompt"], prob["function_name"], prob["tests"]),
      ("Write f.", "f", ["assert f(1) == 2"]))
check("valid: assert(...) form", reason(body(tests=["assert(f(1) == 2)"])), None)

# --- 3. validator: rejections, each with its specific reason ---------
REJECT = {
    "not an object":        ([1, 2], "body must be a JSON object"),
    "missing tests":        ({"prompt": "p", "function_name": "f"}, "missing field(s): tests"),
    "extra timeout":        (body(timeout=30), "unexpected field(s): timeout"),
    "empty prompt":         (body(prompt="   "), "prompt must be a non-empty string"),
    "prompt not str":       (body(prompt=5), "prompt must be a non-empty string"),
    "prompt too long":      (body(prompt="x" * 4001), "prompt longer than 4000"),
    "name starts digit":    (body(function_name="1f"), "function_name must be a valid"),
    "name has dash":        (body(function_name="f-x"), "function_name must be a valid"),
    "name is keyword":      (body(function_name="def"), "function_name must be a valid"),
    "name not str":         (body(function_name=None), "function_name must be a valid"),
    "tests empty":          (body(tests=[]), "tests must be a non-empty list"),
    "tests not list":       (body(tests="assert f(1) == 2"), "tests must be a non-empty list"),
    "too many tests":       (body(tests=["assert f(1) == 2"] * 21), "at most 20 tests"),
    "test not str":         (body(tests=[3]), "tests[0]: must be a string"),
    "test too long":        (body(tests=["assert f(1) == " + "1" * 500]), "tests[0]: longer than 500"),
    "test has \\n":         (body(tests=["assert f(1) == 2\nimport os"]), "tests[0]: must be a single line"),
    "test has \\r":         (body(tests=["assert f(1) == 2\rx"]), "tests[0]: must be a single line"),
    "test has \\u2028":     (body(tests=["assert f(1) == 2 x"]), "tests[0]: must be a single line"),
    "test leading space":   (body(tests=[" assert f(1) == 2"]), "tests[0]: must start with 'assert'"),
    "test not assert":      (body(tests=["f(1)"]), "tests[0]: must start with 'assert'"),
    "test 'asserted ='":    (body(tests=["asserted = f(1)"]), "tests[0]: must be exactly one assert"),
    "test two statements":  (body(tests=["assert f(1) == 2; import os"]), "tests[0]: must be exactly one assert"),
    "test syntax error":    (body(tests=["assert f(1) =="]), "tests[0]: not valid Python"),
    "test no fn reference": (body(tests=["assert g(1) == 2"]), "tests[0]: does not reference"),
    "fn only in a string":  (body(tests=["assert 'f' == 'f'"]), "tests[0]: does not reference"),
    "bad test reported by index": (body(tests=["assert f(1) == 2", "assert f(2) ==\n3"]),
                                   "tests[1]: must be a single line"),
}
for name, (b, want) in REJECT.items():
    r = reason(b)
    check(f"reject: {name}", (r or "").startswith(want), True)

# --- 4. route(): response shaping from stubbed cascade ---------------
def tier(status, passed, code):
    return {"status": status, "passed": passed, "_extracted_code": code} if code is not None \
        else {"status": status, "passed": passed}

def stub_for(cheap, premium=None, raise_exc=None):
    seen = {}
    def stub(problem, cheap_model, premium_model, timeout_s=10, on_tier=None):
        seen.update(models=(cheap_model, premium_model), timeout_s=timeout_s, problem=problem)
        if raise_exc:
            raise raise_exc
        on_tier("cheap", cheap)
        if cheap["passed"]:
            return {"final_model": "cheap", "escalated": False, "final_passed": True,
                    "final_status": cheap["status"], "cheap_status": cheap["status"],
                    "premium_status": None, "cheap_latency_ms": 100,
                    "premium_latency_ms": None, "total_latency_ms": 100}
        on_tier("premium", premium)
        return {"final_model": "premium", "escalated": True, "final_passed": premium["passed"],
                "final_status": premium["status"], "cheap_status": cheap["status"],
                "premium_status": premium["status"], "cheap_latency_ms": 100,
                "premium_latency_ms": 200, "total_latency_ms": 300}
    return stub, seen

KEYS = {"final_model", "escalated", "passed", "final_status", "cheap_status",
        "premium_status", "code", "cheap_latency_ms", "premium_latency_ms",
        "total_latency_ms"}

server.run_cascade_problem, seen = stub_for(tier("PASS", True, "def f(x): return 2"))
out = server.route(server.validate_request(body())[0])
check("cheap-pass: response keys", set(out), KEYS)
check("cheap-pass: decision", (out["final_model"], out["escalated"], out["passed"], out["final_status"]),
      ("cheap", False, True, "PASS"))
check("cheap-pass: premium_status null", out["premium_status"], None)
check("cheap-pass: per-tier latency, premium null",
      (out["cheap_latency_ms"], out["premium_latency_ms"], out["total_latency_ms"]), (100, None, 100))
check("cheap-pass: cheap code returned", out["code"], "def f(x): return 2")
check("fixed models", seen["models"], ("qwen2.5-coder:1.5b", "qwen2.5-coder:7b"))
check("fixed timeout_s=10", seen["timeout_s"], 10)

server.run_cascade_problem, _ = stub_for(tier("VALUE_MISMATCH", False, "cheap code"),
                                         tier("PASS", True, "premium code"))
out = server.route(server.validate_request(body())[0])
check("escalated: decision", (out["final_model"], out["escalated"], out["passed"]),
      ("premium", True, True))
check("escalated: both statuses", (out["cheap_status"], out["premium_status"]),
      ("VALUE_MISMATCH", "PASS"))
check("escalated: PREMIUM code returned", out["code"], "premium code")
check("escalated: per-tier latency passthrough",
      (out["cheap_latency_ms"], out["premium_latency_ms"], out["total_latency_ms"]), (100, 200, 300))

server.run_cascade_problem, _ = stub_for(tier("RUNTIME_ERROR", False, "cheap code"),
                                         tier("VALUE_MISMATCH", False, "premium code"))
out = server.route(server.validate_request(body())[0])
check("both-fail: passed false", out["passed"], False)
check("both-fail: code still returned", out["code"], "premium code")

server.run_cascade_problem, _ = stub_for(tier("VALUE_MISMATCH", False, "cheap code"),
                                         tier("EXTRACTION_FAILED", False, None))
out = server.route(server.validate_request(body())[0])
check("extraction-failed: code null", out["code"], None)
check("extraction-failed: status", out["final_status"], "EXTRACTION_FAILED")

# --- 5. real HTTP layer ----------------------------------------------
srv = server.make_server(port=0, quiet=True)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}/route"

def post(payload=None, raw=None, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    data = raw if raw is not None else json.dumps(payload)
    return requests.post(URL, data=data, headers=h, timeout=5)

server.run_cascade_problem, _ = stub_for(tier("PASS", True, "def f(x): return 2"))
r = post(body())
check("http cheap-pass: 200", r.status_code, 200)
j = r.json()
check("http cheap-pass: body", (j.get("final_model"), j.get("escalated")), ("cheap", False))

server.run_cascade_problem, _ = stub_for(tier("VALUE_MISMATCH", False, "c"), tier("PASS", True, "p"))
r = post(body())
check("http escalated: 200", r.status_code, 200)
j = r.json()
check("http escalated: body", (j.get("final_model"), j.get("escalated"), j.get("code")),
      ("premium", True, "p"))

called = []
server.run_cascade_problem = lambda *a, **k: called.append(1)
for name, b, want in [("bad identifier", body(function_name="1f"), "function_name"),
                      ("newline in test", body(tests=["assert f(1)\n== 2"]), "tests[0]: must be a single line"),
                      ("empty tests", body(tests=[]), "tests must be a non-empty list")]:
    r = post(b)
    check(f"http 422 {name}: status", r.status_code, 422)
    check(f"http 422 {name}: reason", want in r.json().get("error", ""), True)
check("http 422: cascade never called", called, [])

check("http invalid JSON -> 400", post(raw="{not json").status_code, 400)
check("http text/plain -> 415", post(body(), headers={"Content-Type": "text/plain"}).status_code, 415)
check("http foreign Host -> 403", post(body(), headers={"Host": "evil.example"}).status_code, 403)
check("http oversized body -> 413",
      post(raw=json.dumps(body(prompt="x" * (server.MAX_BODY_BYTES + 1)))).status_code, 413)
check("http wrong path -> 404",
      requests.post(f"http://127.0.0.1:{port}/x", json=body(), timeout=5).status_code, 404)

# busy: lock already held -> 503 immediately, with Retry-After
check("lock free before busy test", srv.route_lock.acquire(blocking=False), True)
r = post(body())
check("http busy -> 503", r.status_code, 503)
check("http busy: Retry-After", r.headers.get("Retry-After"), "120")
srv.route_lock.release()

# Ollama down: 502 with a clear reason, and the lock is released
server.run_cascade_problem, _ = stub_for(None, raise_exc=requests.ConnectionError("refused"))
r = post(body())
check("http ollama down -> 502", r.status_code, 502)
check("http 502 reason", r.json().get("error", "").startswith("Ollama call failed: ConnectionError"), True)
server.run_cascade_problem, _ = stub_for(tier("PASS", True, "ok"))
check("http after 502: next request 200, not 503", post(body()).status_code, 200)

server.run_cascade_problem, _ = stub_for(None, raise_exc=requests.ReadTimeout("slow"))
check("http ReadTimeout -> 502", post(body()).status_code, 502)
server.run_cascade_problem, _ = stub_for(None, raise_exc=FileNotFoundError("podman"))
check("http other exception -> 500", post(body()).status_code, 500)
server.run_cascade_problem, _ = stub_for(tier("PASS", True, "ok"))
check("http after 500: next request 200", post(body()).status_code, 200)

# --- 6. hardening, over a raw socket --------------------------------
# Every probe has its own CLIENT-side timeout, so a regression that makes
# the server wait forever shows up as a FAIL, never as a hung test run.
def raw(head: str, payload: bytes = b"", client_timeout: float = 3.0):
    """Send raw request bytes; return (status_code or None, body_text, elapsed_s)."""
    s = socket.create_connection(("127.0.0.1", port))
    s.settimeout(client_timeout)
    t = time.monotonic()
    s.sendall(head.encode() + b"\r\n" + payload)
    data = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        pass
    finally:
        s.close()
    text = data.decode(errors="replace")
    status = int(text.split()[1]) if text.startswith("HTTP/") else None
    return status, text.split("\r\n\r\n", 1)[-1] if status else "", time.monotonic() - t

GOOD = json.dumps(body()).encode()
HEAD = f"POST /route HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
server.run_cascade_problem, _ = stub_for(tier("PASS", True, "ok"))

check("raw: well-formed request -> 200", raw(HEAD + f"Content-Length: {len(GOOD)}\r\n", GOOD)[0], 200)
check("raw: Content-Length missing -> 411", raw(HEAD, GOOD)[0], 411)
check("raw: chunked, no Content-Length -> 411",
      raw(HEAD + "Transfer-Encoding: chunked\r\n",
          f"{len(GOOD):x}\r\n".encode() + GOOD + b"\r\n0\r\n\r\n")[0], 411)
for bad in ["-1", "+5", "1_0", "abc", "", "\u0665"]:   # \u0665 = Arabic-Indic digit five
    st, txt, _ = raw(HEAD + f"Content-Length: {bad}\r\n", GOOD)
    # status AND reason: "+5" or "1_0" through bare int() also ends in a 400,
    # but from the JSON parser after reading the wrong number of bytes
    check(f"raw: Content-Length {bad!r} -> 400 for the length itself",
          (st, json.loads(txt or "{}").get("error")),
          (400, "Content-Length must be a non-negative integer"))

# body shorter than Content-Length: server must give up after RECV_TIMEOUT_S
# with 408 — the client waits a few seconds longer than that, no more
st, txt, took = raw(HEAD + f"Content-Length: {len(GOOD) + 10}\r\n", GOOD,
                    client_timeout=server.RECV_TIMEOUT_S + 5)
check("raw: body shorter than Content-Length -> 408", st, 408)
check("raw: 408 arrives after ~RECV_TIMEOUT_S, not instantly",
      server.RECV_TIMEOUT_S - 1 <= took <= server.RECV_TIMEOUT_S + 3, True)
check("raw: lock untouched by a stalled body", srv.route_lock.locked(), False)

# 500: full traceback on the server's stderr, type only to the client
server.run_cascade_problem, _ = stub_for(None, raise_exc=RuntimeError("secret /home/zoro/path"))
captured, real_stderr = io.StringIO(), sys.stderr
sys.stderr = captured
try:
    st, txt, _ = raw(HEAD + f"Content-Length: {len(GOOD)}\r\n", GOOD)
finally:
    sys.stderr = real_stderr
check("raw 500: status", st, 500)
check("raw 500: client sees type only",
      json.loads(txt or "{}").get("error"), "internal error: RuntimeError (see server log)")
check("raw 500: message not leaked to client", "secret" in txt, False)
check("raw 500: traceback on server stderr",
      "Traceback (most recent call last)" in captured.getvalue()
      and "RuntimeError: secret /home/zoro/path" in captured.getvalue(), True)
server.run_cascade_problem, _ = stub_for(tier("PASS", True, "ok"))
check("raw: after 500 next request 200", raw(HEAD + f"Content-Length: {len(GOOD)}\r\n", GOOD)[0], 200)
check("RECV_TIMEOUT_S is 10", (server.RECV_TIMEOUT_S, server.Handler.timeout), (10, 10))

check("lock free at end", srv.route_lock.locked(), False)
srv.shutdown()
srv.server_close()

check("bound to loopback only", server.HOST, "127.0.0.1")

print(f"{'CASE':<48} RESULT")
for n, ok, got, want in checks:
    if not ok:
        print(f"{n:<48} FAIL  got={got!r} want={want!r}")
print(f"\n{len(checks)-len(fails)}/{len(checks)} checks passed")
sys.exit(1 if fails else 0)
