# WAYPOINT — project context for Claude Code

## What this is
An LLM cost/quality routing gateway, built as a research pipeline first:
Ollama (local models) → code extraction → Podman-sandboxed pytest
execution → structured pass/fail telemetry. Portfolio project for a
CMU-Africa MS Engineering AI application. The point is methodological
rigor, not a working product yet — every claim in this repo has to be
backed by a verified test run, not assumed.

## Current status
**`EXPERIMENTAL_FINDINGS.md` is the full chronological narrative** — what was
assumed at each stage, what the data showed, and what had to be corrected.
Read it there rather than duplicating a summary here that would go stale.

Where things stand:
- **Stage 0 (`pilot_dataset.json`, 12 problems):** DONE. Saturated benchmark —
  both tiers 100%. Retained as the cascade's zero-escalation control.
- **Stress series (`stress_dataset.json`, `stress_001`-`004`):** DONE.
  Aggregate: cheap 10/32 (31.25%), premium 14/32 (43.75%).
- **Stage 1 cascade:** DONE, both datasets. Logs:
  `logs/cascade_stress_log.jsonl`, `logs/cascade_pilot_log.jsonl`.
- **`schema_probe` (experimental shadow path):** RUN. 3/3 pre-registered
  predictions matched; 1/3 recovery. Log: `logs/schema_probe_log.jsonl`.
- Verified test suites: 34 + 29 + 31 = 94 synthetic checks across
  `test_status_taxonomy.py`, `test_cascade_shape.py`, `test_schema_probe.py`.

### Deliberately deferred — NOT forgotten
Both of these are recorded decisions. Do not treat either as an oversight to
be quietly fixed, and do not start either without asking.

- **`stress_005` — known coverage gap, not yet addressed.** `stress_001`'s
  test set does not discriminate a tie-break bug we know is present in the
  recovered premium code (it compares per-record revenue against
  `top_product_revenue` rather than accumulated per-product revenue, and
  passes all 7 tests anyway). Rule 3 forbids adding a discriminating test to
  a problem already run against models, so closing this needs a NEW problem.
  Deferred pending a decision on scope.
- **`schema_probe`-as-a-feature — research hypothesis pending more
  evidence.** Nothing in the production path uses it; it is a diagnostic that
  showed our failure-mode labeling was wrong. Whether output-shape repair
  belongs in a real gateway is open, and specifically pending a dataset where
  more than one problem exercises it.

Other open limitations (sample size, `schema_probe`'s depth=1 limit, the
untested CRASH/TIMEOUT heuristic, the one stale log row, single model family)
are catalogued in `EXPERIMENTAL_FINDINGS.md` under "Known Limitations / Open
Questions". That section is the canonical list.

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
