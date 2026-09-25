# waypoint — pilot runner

## What's actually verified vs. what isn't

This code was written in a sandbox with **no Podman and no Ollama available**,
so honesty about test coverage:

| Component | Status |
|---|---|
| `extraction.py` | **Tested** — 7 scenarios (fenced/raw/wrong-name/no-code/multi-block/syntax-error), all pass |
| `runner.py` orchestration | **Tested with Ollama + Podman mocked out** — record building, log writing, debug-field stripping all verified |
| `sandbox.py` (real Podman execution) | **Not run yet.** Written carefully, but the `podman run` command itself, the volume mount, the resource limits, and the pytest output parsing have never actually executed against a real container. |
| `ollama_client.py` (real Ollama call) | **Not run yet.** The HTTP call shape is standard Ollama API, but unverified against a live server. |

**Treat the first `wp_001` run as a test of this code, not as pilot data.** If it fails, that's expected — this is exactly the kind of infrastructure bug we wanted to catch on one problem instead of on all 24 runs.

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
