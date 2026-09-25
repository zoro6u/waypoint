"""
schema_probe.py — EXPERIMENTAL shadow evaluation path for schema-contract
failures. Not part of the frozen benchmark.

Premium failed stress_001 and stress_002 while leaking bookkeeping keys into
the returned dict (`top_product_revenue`, `total_sum`, `latest_timestamp`).
This module asks one narrow question: if the extra keys are deleted and
NOTHING else is changed, do the frozen tests then pass? That separates "the
arithmetic underneath was right, the output shape was wrong" from "the answer
was wrong."

WHAT THIS DOES NOT DO
---------------------
It does not modify `sandbox.py`, `runner.py`, or either dataset file. The
shadow run reuses `run_in_sandbox` unchanged and runs the SAME frozen tests;
the only thing that differs is that the model's function is wrapped. It never
overwrites `final_pass_rate` — `schema_repair_recovery_rate` is reported as a
separate, explicitly experimental metric.

Output shape is part of the behavior the prompts specify, so a model that
emits extra keys HAS failed the stated task. A "repaired" pass therefore
measures a deliberately weaker benchmark: semantic correctness given shape
forgiveness. It is a diagnostic about WHERE premium fails. It must never be
quoted as premium's pass rate.

_prune is DELETE-ONLY. It never renames a key, coerces a type, adds a missing
key, fills a default, or reorders anything. That restriction is the whole
safety argument: pruning cannot invent a correct answer, only reveal one that
was already there under a wrong shape.

THE METHOD IS NOT GENERAL OVER NESTING DEPTH
--------------------------------------------
This is a real, current limitation, stated plainly rather than papered over.

Key NAMES are derived automatically from the frozen tests and are never
hardcoded. What is NOT general is the DEPTH. The derivation handles exactly
one shape:

    dict[data_key] -> dict[contract_key] -> scalar        (depth=1)

i.e. an outer dict whose keys are data (category names, sensor ids) wrapping
inner dicts whose keys are the contract. That is the shape of stress_001 and
stress_002, and the depth is a fixed assumption, NOT something the code
discovers. A problem returning three levels of nesting would need the
contract level identified, and this code cannot do that.

It therefore REFUSES rather than guessing. Every precondition below is
checked, and any failure returns a verdict instead of a pruned value:

  UNSUPPORTED_SHAPE      the expected values are not dict[k] -> dict[k] ->
                         scalar (e.g. stress_003 returns dict -> list[tuple],
                         stress_004 returns a bare list)
  UNPARSEABLE_EXPECTED   the expected value is not a literal, so it cannot be
                         read statically (stress_003's test_9 builds it with a
                         list comprehension)
  INSUFFICIENT_CASES     fewer than 2 non-empty expected values, so "constant
                         across cases" is not evidence of anything
  AMBIGUOUS_CONTRACT     inner key sets disagree across cases, so the contract
                         cannot be pinned down
  REFUSED_DATA_DEPTH     repairing would require deleting a key at a position
                         classified as DATA — see below

WHY THE DATA/CONTRACT SPLIT IS LOAD-BEARING
-------------------------------------------
Pruning at a data position would erase evidence of a genuine bug and
manufacture a false pass. If a model invented a spurious category, deleting
it to match the expected keys would hide a real logic error. So the outer
level is NEVER pruned, under any circumstance, and a repair that would
require it returns REFUSED_DATA_DEPTH.

The split is derived by intersecting key sets across the non-empty expected
values: a position whose key set is IDENTICAL across all cases is contract; a
position whose key set VARIES is data. For stress_001 the outer keys go
{Tools}, {Cat1,Cat2}, ... (varies -> data) while the inner keys are
{items,revenue,top_product} in every single case (constant -> contract).

Only asserts of the form `assert <function_name>(...) == <literal>` are read.
Other asserts in a problem's test list are not statements about the return
shape at all — stress_003's test_8 asserts on its INPUT list to check for
mutation — and folding those into the derivation would corrupt the contract.
"""

import ast
import json
from pathlib import Path

# verdicts for the derivation step (no contract could be established)
DERIVE_REFUSALS = (
    "UNSUPPORTED_SHAPE",
    "UNPARSEABLE_EXPECTED",
    "INSUFFICIENT_CASES",
    "AMBIGUOUS_CONTRACT",
)
MIN_NONEMPTY_CASES = 2


def _literal_expectations(tests: list, function_name: str) -> tuple:
    """Read the expected value out of each `assert f(...) == <literal>`.

    Returns (expectations, skipped) where `skipped` records why each ignored
    assert was ignored — kept so the caller can report what was NOT used
    rather than silently narrowing the evidence.
    """
    expectations, skipped = [], []
    for i, t in enumerate(tests):
        src = t.strip()
        try:
            node = ast.parse(src).body[0]
        except SyntaxError:
            skipped.append((i, "unparseable_source"))
            continue
        test_expr = getattr(node, "test", None)
        if not isinstance(test_expr, ast.Compare) or len(test_expr.comparators) != 1:
            skipped.append((i, "not_a_single_comparison"))
            continue
        # LHS must be a DIRECT call to the problem's function. stress_003's
        # test_8 is `(merge_bookings(...), b)[1] == [...]` — a subscript, and
        # an assertion about the input, not the return value.
        left = test_expr.left
        if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name)
                and left.func.id == function_name):
            skipped.append((i, "lhs_not_direct_call"))
            continue
        try:
            expectations.append(ast.literal_eval(test_expr.comparators[0]))
        except (ValueError, SyntaxError):
            # e.g. stress_003 test_9 builds the expected value with a
            # comprehension — not statically readable
            skipped.append((i, "expected_not_literal"))
    return expectations, skipped


def derive_contract(tests: list, function_name: str) -> dict:
    """Derive the depth=1 inner contract key set, or refuse.

    Returns {"verdict": "DERIVED", "contract_keys": frozenset, ...}
         or {"verdict": <one of DERIVE_REFUSALS>, "reason": str, ...}
    """
    expectations, skipped = _literal_expectations(tests, function_name)
    info = {"n_expectations": len(expectations), "skipped_tests": skipped}

    if any(not isinstance(e, dict) for e in expectations):
        return {"verdict": "UNSUPPORTED_SHAPE",
                "reason": "at least one expected value is not a dict",
                **info}

    nonempty = [e for e in expectations if e]
    if len(nonempty) < MIN_NONEMPTY_CASES:
        return {"verdict": "INSUFFICIENT_CASES",
                "reason": f"{len(nonempty)} non-empty expected value(s), "
                          f"need >= {MIN_NONEMPTY_CASES} to tell constant from varying",
                **info}

    # every inner value must itself be a flat dict of scalars: this is the
    # depth=1 assumption, checked rather than assumed
    inner_key_sets = []
    for exp in nonempty:
        for v in exp.values():
            if not isinstance(v, dict):
                return {"verdict": "UNSUPPORTED_SHAPE",
                        "reason": f"inner value is {type(v).__name__}, not dict — "
                                  "this module only handles "
                                  "dict[data_key] -> dict[contract_key] -> scalar",
                        **info}
            if any(isinstance(x, (dict, list, tuple, set)) for x in v.values()):
                return {"verdict": "UNSUPPORTED_SHAPE",
                        "reason": "contract dict contains a nested container; "
                                  "depth > 1 is not supported",
                        **info}
            inner_key_sets.append(frozenset(v))

    if len(set(inner_key_sets)) != 1:
        return {"verdict": "AMBIGUOUS_CONTRACT",
                "reason": f"inner key sets disagree across cases: "
                          f"{sorted(set(map(tuple, map(sorted, inner_key_sets))))}",
                **info}

    outer_key_sets = {frozenset(e) for e in nonempty}
    return {
        "verdict": "DERIVED",
        "contract_keys": inner_key_sets[0],
        # recorded so the data classification is auditable, not just asserted
        "outer_varies": len(outer_key_sets) > 1,
        "outer_key_sets": sorted(tuple(sorted(s)) for s in outer_key_sets),
        **info,
    }


def prune(value, contract_keys):
    """DELETE-ONLY repair at depth=1. Returns (pruned_value, report).

    The outer level is never pruned: its keys are data, and deleting there
    could hide a genuine logic error. Only keys inside each inner dict that
    are absent from `contract_keys` are removed.
    """
    report = {"deleted": [], "missing": [], "refused": None}

    if not isinstance(value, dict):
        report["refused"] = "UNSUPPORTED_SHAPE"
        return value, report

    out = {}
    for outer_key, inner in value.items():
        # NB: outer_key is DATA — kept verbatim even if unexpected, so an
        # invented category still fails the test as it should
        if not isinstance(inner, dict):
            report["refused"] = "UNSUPPORTED_SHAPE"
            return value, report
        extra = [k for k in inner if k not in contract_keys]
        absent = [k for k in contract_keys if k not in inner]
        for k in extra:
            report["deleted"].append(f"{outer_key}.{k}")
        for k in absent:
            report["missing"].append(f"{outer_key}.{k}")
        out[outer_key] = {k: v for k, v in inner.items() if k in contract_keys}

    # a missing contract key is NOT a shape violation we can repair — adding it
    # would mean inventing a value. Recorded and left to fail.
    return out, report


def build_wrapper(model_code: str, function_name: str, contract_keys) -> str:
    """Wrap the model's function so its return value is pruned before the
    frozen tests see it. The model's code is included VERBATIM."""
    keys = sorted(contract_keys)
    return f'''{model_code}

# ---- schema_probe shadow wrapper (experimental evaluation path) ----
# The model's code above is unmodified. This only prunes extra keys from the
# returned value; it never renames, coerces, adds, or reorders.
_schema_probe_orig = {function_name}
_SCHEMA_PROBE_CONTRACT = {keys!r}


def {function_name}(*args, **kwargs):
    _out = _schema_probe_orig(*args, **kwargs)
    if not isinstance(_out, dict):
        return _out
    _pruned = {{}}
    for _ok, _inner in _out.items():
        if not isinstance(_inner, dict):
            return _out
        _pruned[_ok] = {{_k: _v for _k, _v in _inner.items()
                        if _k in _SCHEMA_PROBE_CONTRACT}}
    return _pruned
'''
