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
  clean "cheap=logic, premium=schema" split. Aggregate: cheap 10/32
  (31.25%), premium 14/32 (43.75%). n=32 is an exploration signal, not
  a statistical claim about the model family.

  **Why cheap's number changed from 8/32 (25%) to 10/32 (31.25%):**
  this is a MEASUREMENT change, not a model behavior change. The
  evaluator now awards partial credit for tests that actually completed
  before a TIMEOUT cut execution off, where the old code zeroed the
  entire run via a bare `execution_failed` flag. The affected run is
  stress_003 cheap: `test_0` and `test_8` passed, then `test_9` hung,
  so it moved 0.0 -> 0.2 (2/10) and the aggregate moved by those same
  2 tests.

  This was confirmed, not assumed. The generated code for that run is
  byte-identical between the original stress sweep and the Stage 1
  cascade sweep — as is every other cheap and premium generation in
  the set — so temperature=0/seed=42 reproducibility holds across
  sessions and the only thing that differed was how the result was
  scored. The old 8/32 figure was not wrong for the scoring rule in
  place at the time; it undercounted because that rule could not
  distinguish "hung having completed nothing" from "hung partway
  through."
- **Stage 1 (cascade.py, run_cascade.py, analyze_cascade.py):** BUILT
  AND RUN against both datasets. Logs: `logs/cascade_stress_log.jsonl`,
  `logs/cascade_pilot_log.jsonl`.
  - pilot (n=12): cheap 12/12, escalation 0/12, final 12/12. The
    expected sanity check on already-saturated data — the router never
    reached premium.
  - stress (n=4): cheap 0/4, escalation 4/4, premium recovery 1/4,
    final 1/4. Escalated rows cost ~134s total vs ~29s for cheap alone,
    so escalation is roughly 5x the latency for one recovery in four.
  - Recovery by cheap failure type (n=1 per cell — directional only, NOT
    a basis for a Stage 2 routing rule): VALUE_MISMATCH 0/2,
    RUNTIME_ERROR 0/1, TIMEOUT 1/1. The single recovery came from the
    TIMEOUT row.
  - No runner_error rows in either sweep. `ollama_client.py`'s read
    timeout was raised 120s -> 300s first: premium latencies of 81-113s
    were running at up to 98% of the old 120s ceiling, which is what
    killed stress_004 premium in the earlier stress run. This is a
    transport setting, not part of the benchmark definition.
  - Reproducibility confirmed across sessions: every cheap and premium
    generation in the stress cascade is byte-identical to the original
    stress sweep.

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
The Stage 1 cascade sweeps are DONE — see Current status above for the
numbers, and re-read the logs rather than re-running if you only need
the results:

```bash
python analyze_cascade.py --log logs/cascade_stress_log.jsonl
python analyze_cascade.py --log logs/cascade_pilot_log.jsonl
```

Re-running a sweep regenerates committed experimental data and costs
~9 min (stress) / ~2 min (pilot). Generations are byte-identical under
temperature=0/seed=42, so a re-run is only worth it when the LOGGED
FIELDS need to change, not the results.

In progress: `schema_probe.py`, an experimental shadow evaluation path
for the schema-contract failures premium hit on stress_001/002 (extra
keys such as `top_product_revenue` leaking into the returned dict). It
derives the expected key set from the frozen tests, deletes extra keys
only, re-runs the SAME frozen tests via the existing `run_in_sandbox`,
and reports `schema_repair_recovery_rate` as a SEPARATE experimental
metric. It never overwrites `final_pass_rate`, and `sandbox.py`,
`runner.py` and both dataset files stay byte-identical.

Known limit of that method, stated deliberately: the contract-vs-data
key derivation is NOT general over nesting depth. It handles exactly
`dict[data_key] -> dict[contract_key] -> scalar` and refuses anything
else rather than guessing. See the module docstring.

Do NOT start designing Stage 2 or adding new stress problems without
asking first. Outstanding decisions the user has not settled:
- Whether to re-run the stress sweep so stress_003's row picks up the
  now-populated TIMEOUT `failed_tests` (currently `[]` in the committed
  log, pre-fix).
