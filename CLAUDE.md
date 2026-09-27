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
- **`stress_005` (accumulation-before-comparison coverage closer):** DESIGNED,
  APPROVED AND RUN — no longer deferred. Cascade log:
  `logs/cascade_stress_log_v2.jsonl` (5 problems; the committed 4-problem
  `logs/cascade_stress_log.jsonl` is deliberately left untouched;
  `EXPERIMENTAL_FINDINGS.md` now reports the v2 numbers and cites both).
  **Result: premium PASSED 7/7 — it did NOT reproduce the accumulation bug.**
  Cheap failed 3/7 (0.4286) on tests {2,3,4,6}. Five-problem cascade:
  cheap 0/5, escalation 5/5, premium recovery 2/5, final 2/5.
  Two caveats recorded rather than glossed:
  (a) cheap's failing-test fingerprint {2,3,4,6} is *identical* to the
  reference accumulation bug's, but its actual defect is different — it
  accumulates per player correctly and then overwrites the group entry once
  per player instead of aggregating across players (test_2: leader 'B'
  correct, total_points 60 instead of 110). The fingerprint identifies
  "multi-player-per-group handling is broken", not which mechanism broke it.
  (b) premium's code is not in the cascade log (a clean pass drops debug
  fields, rule 6), so it was recovered by a separate single audit run outside
  the cascade: `logs/stress_005_premium_audit.jsonl` (also lean, no code) plus
  `logs/stress_005_premium_audit_console.json` (the only stored copy of the
  code). Same prompt/model/temperature=0/seed=42 and same outcome, so very
  likely the cascade's code, but not verified byte-for-byte. The audited code
  DOES accumulate correctly (sums per player per group, then compares). It
  has a second, untested defect: `player_points[player]` is initialized only
  for the player's first group, so a player appearing in two groups raises
  `KeyError` (`[('A','G1',10), ('A','G2',5)]`). No stress_005 test covers
  that; tests stay frozen (rule 3). Supported claim: "no general accumulation
  blind spot" — NOT "premium's code is correct", and NOT "stress_001 fixed".
- **Stage 1 cascade:** DONE, both datasets. Logs:
  `logs/cascade_pilot_log.jsonl` (pilot, n=12);
  `logs/cascade_stress_log.jsonl` (stress v1, n=4 — first sweep, frozen as
  committed, pre-TIMEOUT-fix `stress_003` row); and
  `logs/cascade_stress_log_v2.jsonl` (stress v2, n=5 — full re-run adding
  `stress_005`; current source for the stress numbers in
  `EXPERIMENTAL_FINDINGS.md`).
- **`schema_probe` (experimental shadow path):** RUN. 3/3 pre-registered
  predictions matched; 1/3 recovery. Log: `logs/schema_probe_log.jsonl`.
- Verified test suites: 34 + 29 + 31 = 94 synthetic checks across
  `test_status_taxonomy.py`, `test_cascade_shape.py`, `test_schema_probe.py`.

### Deliberately deferred — NOT forgotten
This is a recorded decision. Do not treat it as an oversight to be quietly
fixed, and do not start it without asking.

- **`schema_probe`-as-a-feature — research hypothesis pending more
  evidence.** Nothing in the production path uses it; it is a diagnostic that
  showed our failure-mode labeling was wrong. Whether output-shape repair
  belongs in a real gateway is open, and specifically pending a dataset where
  more than one problem exercises it.

Other open limitations (sample size, `schema_probe`'s depth=1 limit, the
untested CRASH/TIMEOUT heuristic, the one stale log row, single model family)
are catalogued in `EXPERIMENTAL_FINDINGS.md` under "Known Limitations / Open
Questions". That section is the canonical list.

NOTE: `EXPERIMENTAL_FINDINGS.md` HAS been updated for `stress_005` (Stress
n=5 section, Limitation 1 update incl. the audit and the KeyError, Limitation
2, and the "pass/fail pattern underdetermines its cause" methodology entry).

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
asking first. Previously outstanding, now settled:
- v2 run supersedes the practical need to regenerate v1 — v1 stays as-is per
  Limitation 5.
