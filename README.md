# waypoint — pilot runner

An LLM cost/quality routing gateway, built as a research pipeline first:
Ollama (local models) → code extraction → Podman-sandboxed pytest execution →
structured pass/fail telemetry.

**For what was actually measured — including the two interpretations that had
to be revised, the evaluator bug that shifted a recorded figure, and the
open limitations — see [EXPERIMENTAL_FINDINGS.md](EXPERIMENTAL_FINDINGS.md).**
The rest of this file is setup and operating instructions.

## What's actually verified vs. what isn't

The code was first written without Podman or Ollama available; both have
since been exercised for real by every sweep recorded in `logs/` (pilot,
stress, cascade, schema_probe). Current coverage:

| Component | Status |
|---|---|
| `extraction.py` | **Tested** — 7 scenarios (fenced/raw/wrong-name/no-code/multi-block/syntax-error), all pass |
| `runner.py` orchestration | **Tested with Ollama + Podman mocked out** — record building, log writing, debug-field stripping all verified |
| `sandbox.py` (real Podman execution) | **Run for real** in every pilot/stress/cascade sweep in `logs/`. Status classification additionally unit-tested on synthetic pytest output (`test_status_taxonomy.py`). The CRASH/TIMEOUT elapsed-time heuristic is still untested against a real hang — see EXPERIMENTAL_FINDINGS.md. |
| `ollama_client.py` (real Ollama call) | **Run for real** against a local Ollama server (`qwen2.5-coder:1.5b` / `:7b`) in every sweep in `logs/`. |
| `cascade.py` | **Tested with `run_one` stubbed** (`test_cascade_shape.py`); `run_cascade` ≡ `run_cascade_problem` on all 17 dataset problems (`test_runner_refactor.py`); run for real in the cascade sweeps. |
| `server.py` (gateway) | **Tested with the cascade stubbed** (`test_server.py`: validator, response shape, real HTTP on loopback, 502/503 lock release). Live: `demo_gateway.py` pre-registers the outcome of two requests built from dataset problems; both held — `wp_001` cheap PASS, not escalated; `stress_005` escalated, premium PASS, returned code byte-identical to the audited premium code. |

**Treat the first `wp_001` run as a test of this code, not as pilot data.** If it fails, that's expected — this is exactly the kind of infrastructure bug we wanted to catch on one problem instead of on all 24 runs.

## Local gateway (`server.py`)

A thin HTTP endpoint over the same Stage 1 cascade. The cascade sweeps
judge quality with each dataset's hidden tests; the gateway has no
hidden tests, so **the caller supplies them**. Routing is unchanged:
cheap first, escalate to premium only when cheap's run isn't a pass.
Models, temperature=0/seed=42 and the 10 s sandbox timeout are fixed
server-side and cannot be set per request. Nothing is written to `logs/`.

```bash
python server.py            # http://127.0.0.1:8765/route

curl -s http://127.0.0.1:8765/route -H 'Content-Type: application/json' -d '{
  "prompt": "Write a function add(a, b) that returns the sum of a and b.",
  "function_name": "add",
  "tests": ["assert add(1, 2) == 3", "assert add(-1, 1) == 0"]
}'
```

Response:

| Field | Meaning |
|---|---|
| `final_model` | `"cheap"` or `"premium"` — the tier whose verdict is final |
| `escalated` | whether premium was called |
| `passed` | whether the final tier passed **all** caller tests |
| `final_status` | `PASS` / `VALUE_MISMATCH` / `RUNTIME_ERROR` / `TIMEOUT` / `CRASH` / `EXTRACTION_FAILED` |
| `cheap_status` | cheap tier's status |
| `premium_status` | premium tier's status, `null` if not escalated |
| `code` | code extracted from the final tier's response — **returned even when `passed` is false** (it is the best attempt, not a verified one); `null` only on `EXTRACTION_FAILED` |
| `cheap_latency_ms` | cheap tier's generation latency |
| `premium_latency_ms` | premium tier's generation latency, `null` if not escalated |
| `total_latency_ms` | generation latency summed over the tiers that ran |

Input rules (violations → `422` with the reason): exactly the three
fields above; `prompt` non-empty, ≤ 4000 chars; `function_name` a valid
Python identifier, not a keyword; `tests` a list of 1–20 strings, each
≤ 500 chars, a **single line** that starts with `assert`, parses as exactly
one `assert` statement, and references `function_name`. (The sandbox
writes each test as one line inside its own `def test_N():`; a multi-line
or non-assert test would break the whole test file and be misreported as
a model CRASH.)

Other responses: `400` invalid JSON, or `Content-Length` not a plain
non-negative integer · `403` Host not `127.0.0.1`/`localhost` · `408` body
not received in time (see below) · `411` no `Content-Length` (chunked bodies
are not supported) · `413` body > 64 KB · `415` Content-Type not
`application/json` · `502` Ollama unreachable or timed out · `500` other
internal error — the response names the exception type only, the full
traceback goes to the server's stderr · `503` another request is already
running (retry after `Retry-After` seconds).

Expect ~5–40 s for a request cheap passes, and one to three minutes for one
that escalates on CPU — it varies with load (e.g. whether Ollama has the
model already loaded). Each tier's generation is capped by `call_ollama`'s
180 s timeout: a premium (or cheap) generation that runs past it returns
`502` by design, not a slower answer. curl has no default timeout; other
clients may need one raised.

### Security limits

This is a local research tool, not a service. Read before running it:

- **It executes caller-supplied code.** The tests sent in the request, and
  the code the model writes, run as real Python inside the Podman sandbox
  (`--network=none`, read-only root, 256 MB, 1 CPU, 64 pids, 10 s). That is
  the *only* isolation. It is not a guarantee against a container escape or
  kernel bug, and whether Podman runs rootless on a given machine has to be
  checked there.
- **Bound to `127.0.0.1` only, hard-coded.** No auth, no rate limit. Any
  local process or user can call it. **Do not expose it to a network or the
  internet** — not via a reverse proxy, port forward, or `0.0.0.0` — without
  a redesign.
- Browser-originated requests are blocked as far as is cheap: a page open in
  a local browser could otherwise POST to localhost, so only
  `application/json` is accepted (forces a CORS preflight this server never
  answers) and the `Host` header must be `127.0.0.1`/`localhost` (defeats DNS
  rebinding).
- **One request at a time.** A single lock serialises the cascade. If the
  client disconnects, the handler does **not** stop: it finishes the whole
  cascade and holds the lock until then, so other requests get `503` for
  the remainder.
- **The 10 s socket timeout is per `recv`, not per request.** A client that
  stops sending mid-body gets `408` after 10 s of silence, and the lock is
  never taken for it. But a client that trickles one byte every few seconds
  keeps resetting that clock and can hold a server thread far longer. It
  cannot hold the cascade lock this way (the body is read before the lock),
  and it only matters because there is no auth — another reason this stays
  on loopback.
- **Worst-case time for one request is far longer than a typical one.** Each tier
  can take up to Ollama's request timeout (`call_ollama` default **180 s**)
  plus the sandbox's outer cap (10 s + 5 s), so an escalated request can hold
  the lock for ≈ 2 × 195 s ≈ 6.5 min before failing.

## Setup (one-time)

```bash
cd waypoint
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# build the sandbox image
podman build -t waypoint-sandbox:latest ./sandbox

# make sure Ollama is running and both models are pulled
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:7b
ollama serve   # if not already running as a service
```

## Run the wp_001 dry run

```bash
python runner.py --problem wp_001 --model qwen2.5-coder:1.5b --tag cheap
```

This prints the full result (including debug fields like the raw model
response / raw sandbox output) to the console, and appends a clean
version (no debug fields) to `logs/pilot_log.jsonl`.

## If something breaks

- **Podman "permission denied" on the volume mount**: almost always
  SELinux on Fedora. The `:Z` suffix on the `-v` mount in `sandbox.py`
  should already handle this — if it doesn't, run `getenforce` to
  confirm SELinux is enforcing and check `journalctl -xe` for `avc:
  denied` lines.
- **`len(matches) != tests_total` → `execution_failed: True`**: pytest
  didn't produce the expected `test_solution.py::test_N PASSED/FAILED`
  lines at all — almost always a collection error (e.g. `solution.py`
  failed to import). Check the (non-persisted) `_raw_output` field
  printed to the console for the actual pytest output.
- **`requests.exceptions.ConnectionError`**: Ollama isn't running, or
  is on a different port.

## Next step after wp_001 works

Run the same command with `--model qwen2.5-coder:7b --tag premium` on
`wp_001` too, confirm both produce sane records, **then** move to
sweeping all 12 problems x 2 models — not before.
