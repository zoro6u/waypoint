# WAYPOINT — project context for Claude Code

## What this is
An LLM cost/quality routing gateway, built as a research pipeline first:
Ollama (local models) → code extraction → Podman-sandboxed pytest
execution → structured pass/fail telemetry. Portfolio project for a
CMU-Africa MS Engineering AI application. The point is methodological
rigor, not a working product yet — every claim in this repo has to be
backed by a verified test run, not assumed.

## Current status
- **Stage 0 (pilot_dataset.json, 12 problems, MBPP + matched
  handwritten):** DONE. Result: qwen2.5-coder:1.5b (cheap) and
  qwen2.5-coder:7b (premium) both scored 100% pass — a "saturated
  benchmark," documented as a real negative result, not hidden.
- **Stress series (stress_dataset.json, 4 problems, composite
  multi-step tasks):** DONE. Revised finding after tracing actual
  generated code (not just pass/fail numbers): cheap fails via
  logic/comprehension bugs on all 4; premium fails via a MIX of
  schema-contract violations (dict output problems) and genuine
  algorithmic bugs (stress_004, graph/topological reasoning) — NOT a
  clean "cheap=logic, premium=schema" split. Aggregate: cheap 8/32
  (25%), premium 14/32 (43.75%). n=32 is an exploration signal, not a
  statistical claim about the model family.
- **Stage 1 (cascade.py, run_cascade.py, analyze_cascade.py):** JUST
  BUILT, not yet run against real data. This is the immediate next
  task — see below.

## Non-negotiable methodology rules
These encode decisions already made deliberately; do not change them
without asking the user first:

1. **temperature=0, seed=42** for every Ollama call (already set in
   `ollama_client.py`) — reproducibility is load-bearing for this
   whole project.
2. **timeout_s=10** for sandbox execution, fixed across ALL problems
   as part of the benchmark definition — never tuned per-problem.
3. **Never edit an existing problem's prompt or hidden tests after
   it has been run against a model.** If a test needs fixing (e.g. the
   Collatz(27)→41 swap for a memorization risk, done BEFORE any model
   saw it), that's fine pre-run. Post-run, add a NEW problem instead —
   this is what keeps results defensible as an experiment.
4. **No algorithm/data-structure hints in problem prompts.** Describe
   required behavior only. The point is testing reasoning, not
   hint-following.
5. **failure_type / status classification is TELEMETRY ONLY in Stage
   1.** `cascade.py`'s router branches on exactly one field —
   `result["passed"]` — nothing else. Don't add failure-type-aware
   routing logic until the user explicitly asks for Stage 2, and even
   then, base it on data from a real cascade run, not assumption.
6. **Debug fields (prefixed `_`) are stripped from persisted logs only
   when the outcome doesn't need them** (PASS with no escalation).
   Preserve this convention in any new logging code — see
   `runner.py`/`run_pilot.py`/`run_cascade.py` for the pattern.
7. **Verify before trusting.** Any new piece of classification/scoring
   logic gets unit-tested against synthetic inputs (see how
   `sandbox.py`'s status taxonomy was tested) before being run against
   real model output. A bug was caught this way already (an int/str
   key mismatch that silently mislabeled every exception as
   "Unknown") — that's the standard to hold new code to.
8. Large or irreversible changes (redesigning the evaluator, changing
   the routing rule, adding a new stress problem) get proposed to the
   user first, with reasoning, before being implemented.

## Key files
- `runner.py` — runs ONE problem against ONE model, returns a
  structured record with a `status` field
  (PASS/VALUE_MISMATCH/RUNTIME_ERROR/TIMEOUT/CRASH/EXTRACTION_FAILED)
- `sandbox.py` — Podman execution + the status classification logic
- `extraction.py` — pulls code out of a raw model response
- `cascade.py` — Stage 1 router: cheap first, escalate to premium only
  on `not passed`
- `run_pilot.py` / `run_cascade.py` — sweep drivers over a dataset
- `analyze_pilot.py` / `analyze_cascade.py` — metrics scripts
- `pilot_dataset.json` / `stress_dataset.json` — the two problem sets
- `README.md` — setup instructions, what's verified vs. not

## Immediate task
Run the Stage 1 cascade against BOTH datasets and report back an
interpreted summary (not raw terminal output) using the metrics
`analyze_cascade.py` produces — cheap_pass_rate, escalation_rate,
premium_recovery_rate, final_pass_rate, latency, and the
failure_type-vs-recovery table:

```bash
python run_cascade.py --dataset stress_dataset.json --log logs/cascade_stress_log.jsonl
python analyze_cascade.py --log logs/cascade_stress_log.jsonl

python run_cascade.py --dataset pilot_dataset.json --log logs/cascade_pilot_log.jsonl
python analyze_cascade.py --log logs/cascade_pilot_log.jsonl
```

Note: pilot_dataset.json problems all had 100% pass rate for BOTH
models in Stage 0, so expect escalation_rate ≈ 0 there — that's a
useful sanity check that the cascade behaves correctly on already-known
data, not a sign something's wrong.

After both sweeps, commit the new log files and any code changes to
git with a clear message, then summarize findings for the user and
ask what they want to look at next — do not automatically start
designing Stage 2 or new stress problems without asking.
