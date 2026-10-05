# WAYPOINT — Experimental Findings

A chronological record of an LLM cost/quality routing experiment: what was
assumed at each stage, what the data actually showed, and what had to be
corrected as a result.

The corrections are not an appendix to this document. They are its subject.
Initial measurements exposed limitations in both the benchmark and the
evaluator before they exposed anything about the models, and the sequence in
which that happened is the substance of the work. A version of this document
that reported only the final numbers would omit most of what was learned.

**Pipeline:** Ollama (local models) → code extraction → Podman-sandboxed
pytest execution → structured pass/fail telemetry.
**Models:** `qwen2.5-coder:1.5b` (cheap tier), `qwen2.5-coder:7b` (premium
tier). `temperature=0`, `seed=42`, sandbox `timeout_s=10` fixed across all
problems.

---

## Stage 0 — Pilot benchmark

**Dataset:** `pilot_dataset.json`, 12 problems (MBPP-derived + matched
handwritten).

**What we assumed.** That a 12-problem set spanning a range of difficulty
would separate a 1.5b model from a 7b model, giving a quality axis against
which routing decisions could be traded off against cost.

**What actually happened.**

| tier | mean pass_rate | mean latency |
|---|---|---|
| cheap (1.5b) | 1.0000 (12/12) | 9,170 ms |
| premium (7b) | 1.0000 (12/12) | 33,972 ms |

Both tiers scored 100%. The premium model cost ~3.7× the latency for
**zero** measurable quality gain.

**What was corrected.** The benchmark, not the hypothesis. A routing
experiment needs variance in quality to study; this set had none at
pass/fail granularity. It was labeled a **saturated benchmark** and recorded
as a real negative result rather than quietly replaced. `analyze_pilot.py`
still prints an explicit warning when the measured gap is ≤ 0.02, so the
saturation condition is detected rather than remembered.

The useful consequence: 12/12 on both tiers is exactly what a correctly
functioning cascade should produce zero escalations on later. That made the
pilot set a **control** for Stage 1 rather than a discarded dead end.

---

## Stress series — building a benchmark with variance

**Dataset:** `stress_dataset.json`, 4 composite multi-step problems
(`stress_001`–`stress_004`), written to require several correct decisions
per problem rather than one.

**What actually happened** (initial stress run, pre-evaluator-revision):

| problem | cheap | premium |
|---|---|---|
| stress_001 | 3/7 (0.4286) | 1/7 (0.1429) |
| stress_002 | 4/8 (0.5) | 2/8 (0.25) |
| stress_003 | 0/10 (0.0, `execution_failed`) | 10/10 (1.0) |
| stress_004 | 1/7 (0.1429) | 1/7 (0.1429) |
| **aggregate** | **8/32 (25%)** | **14/32 (43.75%)** |

The benchmark now had variance. It also produced an infrastructure failure:
`stress_004` premium died with
`ReadTimeout: HTTPConnectionPool(host='localhost', port=11434): Read timed
out. (read timeout=120)` — a data hole, not a model result.

---

## First interpretation — "cheap = logic, premium = schema"

**What we assumed.** Reading the pass/fail numbers alongside a first pass
over the generated code suggested a clean division of labour between failure
modes:

- the **cheap** model fails through logic and comprehension bugs;
- the **premium** model gets the logic right but violates the **output
  contract** — it leaks internal bookkeeping keys into the returned dict.

This was an attractive story. It implied premium's failures were largely
cosmetic and cheaply repairable, and it suggested an obvious Stage 2 routing
rule keyed on failure type.

---

## stress_003 and stress_004 broke that interpretation

**What actually happened** when the generated code was traced per problem
rather than read in aggregate:

- **`stress_003`: premium passed 10/10.** Premium does not always violate
  the schema. On an interval-merging task it was simply correct.
- **`stress_004`: premium failed 1/7 through a genuine algorithmic bug**, not
  a schema violation. Its topological scheduling seeded the ready-queue from
  `in_degree` entries only, so tasks with no dependencies never appeared and
  the queue started empty — it returned `[]` on every non-trivial case. That
  is wrong reasoning about a graph, not a misnamed key.

**What was corrected.** The interpretation. `CLAUDE.md` was revised to record
that premium fails through a **mix** of schema-contract violations (the
dict-output problems) and genuine algorithmic bugs (`stress_004`,
graph/topological reasoning) — explicitly "**NOT a clean 'cheap=logic,
premium=schema' split**."

**Methodological note.** The revision came from tracing actual generated code,
not from the pass/fail numbers, which were identical before and after. The
aggregate 14/32 was compatible with both the clean story and the messy one.
Only the code distinguished them.

---

## Evaluator revision — a measurement bug, found and separated from behavior

Building Stage 1 required knowing *why* a run failed, not just that it did.
That meant a status taxonomy decided at the evaluator: `PASS`,
`VALUE_MISMATCH`, `RUNTIME_ERROR`, `TIMEOUT`, `CRASH` (plus
`EXTRACTION_FAILED`, set pre-execution in `runner.py`).

Writing it surfaced a defect in the *old* scoring rule.

**What we assumed.** That `stress_003` cheap scored 0/10 — it had been logged
`execution_failed: True`, `pass_rate: 0.0`.

**What actually happened.** The old rule treated "number of result lines ≠
number of tests" as total failure. The real pytest output was:

```
test_solution.py::test_0 PASSED    [ 10%]
test_solution.py::test_1 FAILED    [ 20%]
...
test_solution.py::test_8 PASSED    [ 90%]
test_solution.py::test_9
```

`test_0` and `test_8` passed; `test_9` hung and the run was killed mid-test.
Under the taxonomy this is `TIMEOUT` with the partial count preserved: **2/10
= 0.2**, not 0.0.

**What was corrected.** The cheap aggregate moved **8/32 (25%) → 10/32
(31.25%)**.

**Why this is not model drift — and how that was established rather than
assumed.** Every cheap and premium generation in the later cascade sweep is
**byte-identical** to the corresponding generation in the original stress
run. `temperature=0` / `seed=42` reproducibility holds across sessions. The
generated code did not change; only the scoring rule did. The old figure was
not wrong for the rule in place at the time — it undercounted because that
rule could not distinguish "hung having completed nothing" from "hung partway
through."

Three further defects were found and fixed in the same area:

1. **An int/str key mismatch** silently labeled *every* parsed exception
   `"Unknown"`. Caught by a synthetic-input unit test before it touched real
   model output; a regression guard for it is now permanent.
2. **`cascade.py` dropped `failed_tests` entirely.** `sandbox.py` computed a
   per-test `exception_type` and `runner.py` passed it up, but the router's
   explicit field list never copied it. Exception detail could only be
   recovered by re-parsing pytest text — precisely what deciding status at
   the evaluator was meant to avoid. After the fix, `grep exception_type` on
   the stress cascade log went from **0 matches to 32**.
3. **`"Unknown"` was carrying two incompatible meanings.** On a `TIMEOUT`,
   pytest is killed before printing its summary block, so no exception type
   is ever reported — legitimate. On a *completed* run, a missing type is the
   signature of defect (1). These were split into `UNKNOWN_TRUNCATED` and
   `Unknown`, so the distinction is legible from the value rather than from a
   comment.

---

## Stage 1 — the cascade, on real data

**Router.** Cheap first; escalate to premium on failure. The router branches
on **exactly one field**, `result["passed"]`. Nothing else about the cheap
attempt — its status, which tests failed, which exception types — feeds the
escalation decision. Failure-type classification is **telemetry only** at
this stage, deliberately, so that any future failure-type-aware rule is built
on observed data rather than on the interpretation we had already had to
revise once.

Before the sweep, `ollama_client.py`'s read timeout was raised **120 s → 180
s**. Premium latencies of 85,016–117,887 ms were running at up to **98% of
the old ceiling**, which is what had killed `stress_004` premium earlier.
This is a transport setting, not part of the benchmark definition, and
`latency_ms` is still recorded truthfully.

> **Correction (2026-10-05).** This paragraph, and the message of commit
> `03e226e`, originally said the timeout was raised to **300 s**. The
> committed code says otherwise: `call_ollama`'s `timeout_s` default is 120
> in `c497936` and **180** in `03e226e` and every commit since, and no caller
> overrides it (`runner.py` calls `call_ollama` without `timeout_s`). Git
> records only committed code, so it cannot prove which value was in the
> working tree at the moment each sweep ran. The discrepancy does not affect
> any recorded result: no cascade run came near either ceiling — the highest
> per-tier latency in the committed cascade logs is 117,887 ms (v1; v2's is
> 109,897 ms), and neither log contains a `runner_error`.

### Pilot (n=12) — the control

| metric | value |
|---|---|
| cheap_pass_rate | 12/12 = 1.000 |
| escalation_rate | **0/12 = 0.000** |
| final_pass_rate | 12/12 = 1.000 |
| mean cheap latency | 9,337 ms (range 2,872–16,481) |

Zero escalations on a set already known to be saturated. The router never
reached premium. This is the sanity check the saturated benchmark was kept
for, and it passed.

### Stress (n=5) — the signal

Source: `logs/cascade_stress_log_v2.jsonl`, a full re-run that adds
`stress_005` (see Known Limitations #1). The first sweep,
`logs/cascade_stress_log.jsonl` (n=4: premium_recovery_rate and
final_pass_rate both 1/4 = 0.250), is kept unchanged as committed. On
`stress_001`–`004` the re-run reproduced every status, pass rate, failed-test
list and extracted code. The only differences are latencies, pytest's
reported run time, and `stress_003`'s `cheap_failed_tests`. That field is
now populated because this run came after the `TIMEOUT` fix (see
Limitation 5).

| metric | value |
|---|---|
| cheap_pass_rate | 0/5 = 0.000 |
| escalation_rate | 5/5 = 1.000 |
| premium_recovery_rate | 2/5 = 0.400 |
| final_pass_rate | 2/5 = 0.400 |
| mean cheap latency | 24,521 ms |
| mean total latency, escalated rows | 122,970 ms |

Escalation costs roughly **5× the cheap-only latency** and bought two
recoveries in five.

Per problem:

| problem | cheap | premium | final |
|---|---|---|---|
| stress_001 | RUNTIME_ERROR 0.4286 | VALUE_MISMATCH 0.1429 | fail |
| stress_002 | VALUE_MISMATCH 0.5 | VALUE_MISMATCH 0.25 | fail |
| stress_003 | TIMEOUT 0.2 | **PASS 1.0** | **pass** |
| stress_004 | VALUE_MISMATCH 0.1429 | VALUE_MISMATCH 0.1429 | fail |
| stress_005 | VALUE_MISMATCH 0.4286 | **PASS 1.0** | **pass** |

### Recovery by cheap failure type

| cheap_status | count | premium recovery |
|---|---|---|
| VALUE_MISMATCH | 3 | 1/3 |
| RUNTIME_ERROR | 1 | 0/1 |
| TIMEOUT | 1 | 1/1 |

On the n=4 sweep, the single recovery came from the `TIMEOUT` row. That
suggested a cheap model *hanging* might say less about problem difficulty
than a cheap model confidently returning wrong values. `stress_005` adds a
recovery from a `VALUE_MISMATCH` row, which already weakens that reading.
Every cell still holds one to three observations. That is not a basis for a
routing rule, so this is recorded as a question, not a result.

Note also that premium's recovery on `stress_003` is a case where the cheap
tier timed out on a 50,000-element input while the premium tier did not:
partly a reasoning difference, partly an efficiency one. The current
telemetry cannot separate those.

---

## schema_probe — testing the first interpretation precisely

The "premium = schema violation" story had already been revised once, but it
had never been *measured*. `schema_probe.py` was built to test it directly:
if the extra keys are deleted and **nothing else** is changed, do the frozen
tests then pass?

**Design constraints, all enforced rather than intended:**

- A **shadow evaluation path**, not a change to the evaluator. `sandbox.py`,
  `runner.py`, `pilot_dataset.json` and `stress_dataset.json` remain
  byte-identical. The probe reuses `run_in_sandbox` unchanged and runs the
  **same frozen tests**; only the model's function is wrapped.
- The expected key set is **derived from the frozen tests by AST**, never
  hand-declared — so the contract was not chosen after seeing who failed.
  Contract keys are separated from data keys by intersecting key sets across
  test cases: a position whose key set is constant across cases is contract,
  one that varies is data.
- `prune()` is **delete-only**. No rename, coerce, add, fill, or reorder. It
  cannot invent a correct answer, only reveal one already present under a
  wrong shape.
- The **outer (data) level is never pruned**. Deleting an invented category
  to make the shape match would erase a genuine logic error and manufacture
  a false pass. This was the primary risk in the design, and it is the one
  the structure guards against.
- `schema_repair_recovery_rate` is reported as a **separate experimental
  metric**. The frozen `final_pass_rate` is never overwritten.

**Prediction registered in code before the run** (`PREDICTION` dict in
`run_schema_probe.py`), so the comparison could not be reconstructed
afterwards. Headline expectation: **"0–1 recoveries out of 3, NOT 2."**

### Results — 3/3 predictions matched

| problem | predicted | actual | match |
|---|---|---|---|
| stress_001 | RECOVERED (hedged) | RECOVERED | yes |
| stress_002 | STILL_FAILED | STILL_FAILED | yes |
| stress_004 | UNSUPPORTED_SHAPE | UNSUPPORTED_SHAPE | yes |

```
schema_repair_recovery_rate:          1/3 = 0.333   (EXPERIMENTAL)
final_pass_rate (FROZEN, unchanged):  1/4 = 0.250
final_pass_rate_if_repaired (EXPT):   2/4 = 0.500
```

**Audit of the one recovery.** `stress_001` moved VALUE_MISMATCH 0.1429 →
**PASS 1.0**. Contract derived as `{items, revenue, top_product}`. The only
key deleted was the leaked bookkeeping key `top_product_revenue`. The outer
level was correctly classified as data — its key sets varied across four
distinct sets (`['Tools']`, `['Cat1']`, `['Cat1','Cat2']`,
`['cat0','cat1']`) — and was never pruned.

**`stress_002` needed an extra check.** Its pass_rate was identical before
and after (0.25 → 0.25), which is also what a silently no-op'ing wrapper
would produce. Verified directly that the prune *did* act:

```
premium raw output : {'A': {'count': 2, 'total_sum': 30, 'latest_timestamp': 3, 'average': 15}}
after prune        : {'A': {'count': 2, 'average': 15}}
deleted            : ['A.total_sum', 'A.latest_timestamp']
missing            : ['A.latest']
expected by test_2 : {'A': {'count': 2, 'average': 15, 'latest': 20}}
```

It still fails because the contract key `latest` is **absent** — premium
stored the *timestamp* rather than the latest *reading*. Delete-only
correctly refuses to invent it.

**`stress_004`** was refused at derivation ("at least one expected value is
not a dict") and never executed — the intended behaviour of a no-op control.

---

## Final revised finding

Of premium's three failures on the stress set, **exactly one** was genuinely
a schema-contract problem:

| problem | actual failure character |
|---|---|
| stress_001 | genuine schema violation — correct arithmetic, leaked key |
| stress_002 | **semantic failure wearing schema clothing** — stored the wrong quantity under a differently-named key |
| stress_004 | genuine algorithmic failure — wrong graph reasoning |

**"Schema-contract violation" overstated how much of premium's failure was
cosmetic.** This is a negative result about our own labeling, and it is the
second time that labeling had to be revised: first from "cheap=logic,
premium=schema" to "premium fails via a mix," then from "a mix" to "only one
of three is actually schema."

The interpretation narrowed each time it was checked against evidence. The
pass/fail numbers never changed.

---

## Known Limitations / Open Questions

Everything below is documented as pending. None of it is resolved.

**1. `stress_001`'s test set does not discriminate a bug we know is there —
partly answered by `stress_005`, not closed.**
The hedge on `stress_001` was that its `top_product` tie-break compares
per-record revenue against `top_product_revenue` rather than accumulated
per-product revenue. It passed all 7 tests anyway. The repaired code may
still be wrong in a way those tests cannot catch, so the `RECOVERED` verdict
is only as strong as the test set behind it. Adding a discriminating test to
a problem already run against models is disallowed, so this needed a **new**
problem.

*Update — `stress_005`.* That problem moves stress_001's accumulate-then-compare
pattern into a new domain (a leaderboard instead of sales). Before any model
saw it, test_2 was checked and does catch a reference implementation of the
stress_001 bug. **Premium passed 7/7.** Five-problem cascade
(`logs/cascade_stress_log_v2.jsonl`): premium_recovery_rate 1/4 → **2/5**,
final_pass_rate 1/4 → **2/5**.

What this supports, and only this: **premium has no general accumulation
blind spot.** Its audited stress_005 code sums each player's entries within a
group (`player_points[player][group] += points`) over all entries first. Only
then does it compare totals within each group.

What it does **not** support:
- **That the stress_001 bug is fixed.** stress_001's premium code is still
  wrong in the way described above. stress_005 is a different problem and a
  different generation, so it tells us nothing about that code.
- **That premium's stress_005 code is correct in general.** The audit found a
  second defect that no frozen test exercises. `player_points[player]` is
  set up only for the first group a player appears in, so a player who
  appears in two groups raises `KeyError`
  (`[('A','G1',10), ('A','G2',5)]` → `KeyError: 'G2'`). No stress_005 test
  has the same player in two groups, and the prompt does not forbid it. So
  the 7/7 is limited by its test set in the same way stress_001's was. Its
  tests stay frozen (rule 3).

*How the code was obtained.* The cascade row for stress_005 does **not**
contain premium's code. A clean PASS strips debug fields from persisted
logs (the lean-log convention), so the cascade log records only that premium
passed. The code was recovered by a **separate single run, outside the
cascade**:
`python runner.py --problem stress_005 --model qwen2.5-coder:7b --tag premium
--dataset stress_dataset.json --log logs/stress_005_premium_audit.jsonl`.
That run's persisted log strips the code for the same reason, so the code is
preserved only from its console output
(`logs/stress_005_premium_audit_console.json`). The audit run used the same
prompt, model, temperature=0 and seed=42, and got the same outcome (PASS,
7/7). It is therefore very likely the same code the cascade produced, but
that is inferred from the reproducibility check, not verified byte for byte:
the cascade's code was never stored. Latency differed (90,853 ms vs
134,761 ms). That is expected, because determinism covers the generated
text, not wall-clock time.

**2. Sample size.** n=5 problems / n=39 test assertions on the stress set;
n=12 on the pilot. The recovery-by-failure-type table has **one to three
observations per cell**. These are **exploration signals, not statistical claims** about
the model family. No confidence interval is computed because none would be
meaningful at this n.

**3. `schema_probe`'s derivation is not general over nesting depth.** It
handles exactly `dict[data_key] -> dict[contract_key] -> scalar`; `depth=1`
is a fixed assumption the code does not discover. Key *names* are derived
automatically, but the depth is not. It **refuses rather than guessing**
(`UNSUPPORTED_SHAPE`, `UNPARSEABLE_EXPECTED`, `INSUFFICIENT_CASES`,
`AMBIGUOUS_CONTRACT`, `REFUSED_DATA_DEPTH`), so the limitation manifests as
an abstention, never a silent wrong answer. Generalizing it soundly would
need more test cases per problem than the current dataset reliably provides.

**4. The `CRASH` vs `TIMEOUT` split has never been exercised against a real
crash.** With zero result lines, elapsed wall-clock time against
`TIMEOUT_FRACTION_THRESHOLD = 0.8` is the only available signal to tell an
import-time exception from a silent hang. That is an approximation, not a
certainty. It is covered by synthetic tests; **no real `CRASH` has occurred
yet**, so the first one should be treated as a test of that logic rather than
as data.

**5. One stale row in the first committed stress log (v1 only).** This
applies specifically to `logs/cascade_stress_log.jsonl` (v1, n=4). It still
holds `stress_003`'s pre-fix row with `cheap_failed_tests: []`, from before
the `TIMEOUT` branch was changed to keep verdicts for tests that completed.
Regenerating v1 in place would rewrite committed experimental data, and
`stress_003` is outside the schema_probe scope anyway (premium passed it
without a failed escalation). **v1 is consciously left as-is.**
`logs/cascade_stress_log_v2.jsonl` (n=5, the source for the Stress section
above) does **not** have this gap. It was generated after the `TIMEOUT` fix,
so its `stress_003` row has all seven `cheap_failed_tests` entries
(`UNKNOWN_TRUNCATED`). The gap is therefore limited to v1. It was not fixed
by editing v1.

**6. Single model family, single seed.** Everything here is
`qwen2.5-coder:1.5b` vs `qwen2.5-coder:7b` at `seed=42`. Reproducibility
across sessions is verified; **generalization across model families, sizes,
or seeds is not tested at all.**

**7. `stress_003`'s cheap timeout conflates two things.** The cheap tier
timed out on a 50,000-element input where premium did not. Current telemetry
cannot separate "reasoned worse" from "ran slower." A `TIMEOUT` is not
currently evidence of either on its own.

**8. `schema_probe`-as-a-feature is a research hypothesis, not a component.**
Nothing in the production path uses it. Whether output-shape repair belongs
in a real gateway — as opposed to being a diagnostic that told us our
labeling was wrong — is **pending more evidence**, and specifically pending
a dataset where more than one problem exercises it.

---

## On the methodology

The routing results above are thin by design — n=5 is an exploration signal.
The transferable part of this project is the discipline that produced them.

**Predictions were registered before runs.** The `schema_probe` expectation
("0–1 of 3, NOT 2", per-problem verdicts) was committed to code *before* the
probe touched real output. A prediction written afterwards cannot be wrong,
and therefore cannot teach anything.

**Observation was kept separate from interpretation.** The Stage 1 router
branches on exactly one field, `result["passed"]`. Rich failure-type
telemetry is recorded and deliberately **not** acted on. This is why the
interpretation could be revised twice without any routing logic needing to
change — the two were never entangled.

**Reproducibility was verified, not assumed.** `temperature=0` / `seed=42`
was checked by confirming that generations are byte-identical across
sessions. That check is what made it possible to state that the 8/32 → 10/32
shift was a *measurement* change rather than model drift — a claim that would
otherwise have been guesswork.

**Classification logic was unit-tested against synthetic inputs before
touching real model output.** Currently 34 + 29 + 31 = **94 checks** across
`test_status_taxonomy.py`, `test_cascade_shape.py` and
`test_schema_probe.py`. This caught the int/str key mismatch that had
silently mislabeled every exception. Seven of the `schema_probe` checks are
**mandatory refusal cases** — a probe that prunes where it should abstain
would manufacture false recoveries, so the abstentions are tested as
carefully as the successes.

**Tests and prompts were frozen once run.** No problem's prompt or hidden
tests were edited after a model had seen them. Where a fix was needed
post-run, the rule is to add a **new** problem instead — which is why the
`stress_001` coverage gap was addressed by a new problem, `stress_005`,
rather than patched in place. The same rule applies to `stress_005` itself:
the multi-group `KeyError` found in premium's passing code is recorded as a
limitation, not fixed by adding a test.

**Negative results were documented, not hidden.** The saturated pilot
benchmark is recorded as a finding with a detector attached, not quietly
replaced with something more flattering. Both revisions of the
failure-mode interpretation are recorded with the superseded version intact.

**Experimental changes were made as parallel paths, never as edits to the
evaluator.** `schema_probe` runs the same frozen tests through the same
sandbox; the frozen `final_pass_rate` is never overwritten by the
experimental one. The two numbers are always reported side by side.

**Results were built to be auditable.** Every `RECOVERED` verdict carries the
exact list of deleted keys. The `stress_002` check exists specifically to
distinguish "the repair ran and did not help" from "the repair silently did
nothing" — two outcomes that are identical in the metrics and completely
different in meaning.

**A pass/fail pattern underdetermines its cause.** Different mechanisms can
produce the same metrics. Two cases are documented above: `stress_002`'s
unchanged 0.25 → 0.25 under schema_probe, which a silent no-op would also
have produced, and `stress_002`'s "schema" label, which turned out to be a
semantic failure. A third comes from `stress_005`. Cheap failed exactly tests
{2, 3, 4, 6}, the same set the reference stress_001-style bug fails. Its
mechanism is the reverse of that bug. The reference bug compares players
without first summing each player's entries. Cheap sums each player's
entries correctly but never compares players: it overwrites the group's
entry once per player, so the last player processed wins. On test_2 it got
`leader` 'B' right only because B came last, and `total_points` was 60
instead of 110. The fingerprint tells you that "multiple players in one
group" breaks the code, not which step broke it.

**Refusal was preferred over guessing.** Where the method's preconditions do
not hold, the code abstains with a named verdict rather than producing a
number that would look the same as a real one.

---

*Every figure in this document comes from a committed log or a verified test
run. Where something is unverified, it is listed above as a limitation.*
