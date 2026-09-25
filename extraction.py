"""
extraction.py — pull runnable Python code out of a raw model response.

This module owns exactly one job: decide whether we can find valid,
parseable code that defines the required function, and hand back that
code. It does NOT run or grade the code — that is sandbox.py's job.

This separation matters for the experiment: "the model wrote broken
Python" (extraction_failed) and "the model's Python ran but gave wrong
answers" (tests_failed, handled downstream) are different failure
modes and must never be collapsed into one number.
"""

import ast
import re

FENCE_RE = re.compile(r"```(?:python)?[ \t]*\n(.*?)```", re.DOTALL)


def _defines_function(code: str, function_name: str) -> bool:
    """True if `code` parses as valid Python and defines a function
    (top-level or nested) named `function_name`."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return True
    return False


def _try_raw_slice(response: str, function_name: str):
    """Fallback for responses with no code fences at all: slice from
    the first `def {function_name}(`, pulling in any immediately
    preceding import lines, and extending forward while lines still
    look code-shaped (blank, indented, or another def/class/import).
    Stops at the first line that reads like prose."""
    marker = f"def {function_name}("
    idx = response.find(marker)
    if idx == -1:
        return None

    lines_before = response[:idx].splitlines()
    start = idx
    while lines_before and (
        lines_before[-1].strip().startswith(("import ", "from ")) or lines_before[-1].strip() == ""
    ):
        start -= len(lines_before[-1]) + 1
        lines_before.pop()
    start = max(start, 0)

    remainder = response[idx:].splitlines(keepends=True)
    collected = []
    for i, line in enumerate(remainder):
        stripped = line.strip()
        code_like = (
            stripped == ""
            or line.startswith((" ", "\t"))
            or stripped.startswith(("def ", "class ", "import ", "from ", "@", "#"))
        )
        if i == 0 or code_like:
            collected.append(line)
        else:
            break
    return response[start:idx] + "".join(collected)


def extract_python_code(response: str, function_name: str) -> dict:
    """
    Try, in order of preference:
      1. Each fenced ```python / ``` code block in the response.
      2. A raw slice starting at `def {function_name}(` for responses
         with no fences at all.

    Returns {"success": bool, "code": str | None, "reason": str | None}.
    `reason` is only set on failure: "function_not_found" (the model
    never wrote the required function at all) or "unparseable" (code
    exists but we could not isolate a clean, valid definition of it).
    """
    for block in FENCE_RE.finditer(response):
        candidate = block.group(1)
        if _defines_function(candidate, function_name):
            return {"success": True, "code": candidate, "reason": None}

    sliced = _try_raw_slice(response, function_name)
    if sliced is not None and _defines_function(sliced, function_name):
        return {"success": True, "code": sliced, "reason": None}

    if f"def {function_name}(" not in response:
        return {"success": False, "code": None, "reason": "function_not_found"}
    return {"success": False, "code": None, "reason": "unparseable"}
