"""
Synthetic-input test of schema_probe's contract derivation and delete-only
prune, per CLAUDE.md rule 7. No Ollama, no Podman, no real model output.

The REFUSAL cases are the point of this file, not an afterthought: a probe
that prunes where it should abstain would manufacture false recoveries.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from schema_probe import build_wrapper, derive_contract, prune

checks, fails = [], []
def check(name, got, want):
    ok = got == want
    checks.append((name, ok, got, want))
    if not ok:
        fails.append(name)

# ===================================================================
# 1. DERIVATION on the real stress_001 test shape -> must DERIVE
# ===================================================================
S1 = [
    "assert summarize_sales([]) == {}",
    "assert summarize_sales([('Widget','Tools',5,10)]) == {'Tools': {'items': 5, 'revenue': 50, 'top_product': 'Widget'}}",
    "assert summarize_sales([('A','Cat1',1,10), ('B','Cat2',2,5)]) == {'Cat1': {'items': 1, 'revenue': 10, 'top_product': 'A'}, 'Cat2': {'items': 2, 'revenue': 10, 'top_product': 'B'}}",
]
d = derive_contract(S1, "summarize_sales")
check("s001: verdict", d["verdict"], "DERIVED")
check("s001: contract keys derived, not hardcoded",
      d["contract_keys"], frozenset({"items", "revenue", "top_product"}))
check("s001: outer classified as DATA (varies)", d["outer_varies"], True)

# real stress_002 shape
S2 = [
    "assert sensor_summary([]) == {}",
    "assert sensor_summary([('A','10',1)]) == {'A': {'count': 1, 'average': 10, 'latest': 10}}",
    "assert sensor_summary([('A','10',1),('A','ERR',2),('A','20',3)]) == {'A': {'count': 2, 'average': 15, 'latest': 20}}",
]
d2 = derive_contract(S2, "sensor_summary")
check("s002: verdict", d2["verdict"], "DERIVED")
check("s002: contract keys", d2["contract_keys"], frozenset({"count", "average", "latest"}))

# ===================================================================
# 2. REQUIRED REFUSALS — each must abstain, never prune
# ===================================================================
# (a) stress_003: dict -> list[tuple], not dict -> dict
S3 = [
    "assert merge_bookings([]) == {}",
    "assert merge_bookings([(8,10,'A'),(1,3,'A')]) == {'A': [(1, 3), (8, 10)]}",
    "assert merge_bookings([(1,3,'A'),(2,4,'B')]) == {'A': [(1, 3)], 'B': [(2, 4)]}",
]
d3 = derive_contract(S3, "merge_bookings")
check("REFUSE s003: unsupported shape", d3["verdict"], "UNSUPPORTED_SHAPE")

# (b) stress_004: bare list, no dict at all
S4 = [
    "assert schedule_tasks([]) == []",
    "assert schedule_tasks([('A',5,[])]) == [('A', 0, 5)]",
]
d4 = derive_contract(S4, "schedule_tasks")
check("REFUSE s004: unsupported shape (no-op control)",
      d4["verdict"], "UNSUPPORTED_SHAPE")

# (c) expected value not a literal (stress_003 test_9 comprehension)
NONLIT = [
    "assert f([]) == {}",
    "assert f([1]) == {'A': {'x': 1}}",
    "assert f([2]) == {'A': {'x': i} for i in range(1)}",
]
d5 = derive_contract(NONLIT, "f")
check("REFUSE: non-literal expected is skipped, not guessed",
      any(r == "expected_not_literal" for _, r in d5["skipped_tests"]), True)

# (d) only one non-empty case -> constant proves nothing
THIN = ["assert f([]) == {}", "assert f([1]) == {'A': {'x': 1, 'y': 2}}"]
d6 = derive_contract(THIN, "f")
check("REFUSE: insufficient cases", d6["verdict"], "INSUFFICIENT_CASES")

# (e) inner key sets disagree -> contract not pinnable
AMBIG = [
    "assert f([1]) == {'A': {'x': 1, 'y': 2}}",
    "assert f([2]) == {'A': {'x': 1, 'z': 3}}",
]
d7 = derive_contract(AMBIG, "f")
check("REFUSE: ambiguous contract", d7["verdict"], "AMBIGUOUS_CONTRACT")

# (f) depth > 1 must refuse, NOT silently prune the wrong level
DEEP = [
    "assert f([1]) == {'A': {'inner': {'x': 1}}}",
    "assert f([2]) == {'B': {'inner': {'x': 2}}}",
]
d8 = derive_contract(DEEP, "f")
check("REFUSE: depth>1 refused rather than assumed",
      d8["verdict"], "UNSUPPORTED_SHAPE")

# (g) an assert on the INPUT (stress_003 test_8) must not feed the contract
MUTCHECK = [
    "assert f([1]) == {'A': {'x': 1, 'y': 2}}",
    "assert f([2]) == {'B': {'x': 2, 'y': 3}}",
    "assert (f(b := [(8,10,'A')]), b)[1] == [(8, 10, 'A')]",
]
d9 = derive_contract(MUTCHECK, "f")
check("REFUSE: input-mutation assert excluded from derivation",
      d9["verdict"], "DERIVED")
check("REFUSE: contract unpolluted by the input assert",
      d9["contract_keys"], frozenset({"x", "y"}))
check("REFUSE: exclusion reason recorded",
      any(r == "lhs_not_direct_call" for _, r in d9["skipped_tests"]), True)

# ===================================================================
# 3. PRUNE — delete-only, and never at the data level
# ===================================================================
C1 = frozenset({"items", "revenue", "top_product"})

# premium's real stress_001 defect: one extra bookkeeping key
got, rep = prune(
    {"Tools": {"items": 5, "revenue": 50, "top_product": "Widget",
               "top_product_revenue": 30}}, C1)
check("prune: extra key deleted",
      got, {"Tools": {"items": 5, "revenue": 50, "top_product": "Widget"}})
check("prune: deletion recorded for audit",
      rep["deleted"], ["Tools.top_product_revenue"])
check("prune: nothing reported missing", rep["missing"], [])

# THE CRITICAL SAFETY CASE: an invented outer key is DATA. Pruning it would
# hide a real logic error and manufacture a false pass.
got, rep = prune(
    {"Tools": {"items": 5, "revenue": 50, "top_product": "W"},
     "InventedCategory": {"items": 1, "revenue": 1, "top_product": "X"}}, C1)
check("prune: invented DATA key survives (would hide a real bug)",
      set(got), {"Tools", "InventedCategory"})
check("prune: no data-level deletion recorded", rep["deleted"], [])

# premium's real stress_002 defect: extras AND a missing contract key.
# Delete-only cannot fix a missing key — must not invent `latest`.
C2 = frozenset({"count", "average", "latest"})
got, rep = prune(
    {"A": {"count": 2, "total_sum": 30, "latest_timestamp": 3, "average": 15}}, C2)
check("prune: extras deleted on s002 shape",
      got, {"A": {"count": 2, "average": 15}})
check("prune: missing contract key reported, not invented",
      rep["missing"], ["A.latest"])
check("prune: never adds a key", "latest" in got["A"], False)

# delete-only: values are never coerced or renamed
got, _ = prune({"A": {"count": "2", "average": 15, "latest": 20, "junk": 1}}, C2)
check("prune: value types untouched (no coercion)", got["A"]["count"], "2")

# already-correct output is left exactly as-is
clean = {"A": {"count": 1, "average": 10, "latest": 10}}
got, rep = prune(clean, C2)
check("prune: correct output unchanged", got, clean)
check("prune: no-op recorded as no deletions", rep["deleted"], [])

# non-dict returns are refused, not mangled (stress_004's bare list)
got, rep = prune([("A", 0, 5)], C1)
check("prune: non-dict refused", rep["refused"], "UNSUPPORTED_SHAPE")
check("prune: non-dict returned untouched", got, [("A", 0, 5)])

# ===================================================================
# 4. WRAPPER — model code verbatim, prune applied, tests untouched
# ===================================================================
model = ("def summarize_sales(records):\n"
         "    return {'Tools': {'items': 5, 'revenue': 50,\n"
         "                      'top_product': 'Widget', 'top_product_revenue': 30}}\n")
wrapped = build_wrapper(model, "summarize_sales", C1)
check("wrapper: model code included verbatim", model in wrapped, True)
ns = {}
exec(compile(wrapped, "<wrapper>", "exec"), ns)
check("wrapper: prunes at runtime",
      ns["summarize_sales"]([]),
      {"Tools": {"items": 5, "revenue": 50, "top_product": "Widget"}})

# the wrapper must also leave invented outer keys alone
model2 = ("def f(x):\n"
          "    return {'A': {'p': 1, 'junk': 9}, 'Invented': {'p': 2}}\n")
ns2 = {}
exec(compile(build_wrapper(model2, "f", frozenset({"p"})), "<w2>", "exec"), ns2)
check("wrapper: invented outer key preserved",
      ns2["f"](0), {"A": {"p": 1}, "Invented": {"p": 2}})

# a non-dict return passes straight through the wrapper
ns3 = {}
exec(compile(build_wrapper("def g(x):\n    return [1, 2]\n", "g",
                           frozenset({"p"})), "<w3>", "exec"), ns3)
check("wrapper: non-dict return passed through", ns3["g"](0), [1, 2])

print(f"{'CASE':<58} RESULT")
for name, ok, got_, want_ in checks:
    print(f"{name:<58} {'ok' if ok else 'FAIL'}"
          + ("" if ok else f"  got={got_!r} want={want_!r}"))
print(f"\n{len(checks)-len(fails)}/{len(checks)} checks passed")
if fails:
    print("FAILED: " + ", ".join(fails))
    sys.exit(1)
print("schema_probe derivation + delete-only prune verified on synthetic inputs")
