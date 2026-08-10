"""Regression guard for a real bug found in Phase 1: tree-sitter core 0.26.0
alongside tree-sitter-python/-javascript 0.25.0 (ABI 15) and
tree-sitter-typescript 0.23.2 (ABI 14 — its latest release) caused native
segfaults (SIGBUS) rather than a clean error. pyproject.toml now pins all four
packages to the matching ABI-14 generation; this test's job is to fail loudly
if that pin ever drifts back out of sync, instead of crashing the interpreter.
"""

from app.analysis.parser import parse_source
from app.ingestion.language_detect import Language


def test_parses_python_without_crashing() -> None:
    result = parse_source(b"def f():\n    return 1\n", "f.py", Language.python)
    assert result is not None
    assert any(u.unit_type == "function" and u.name == "f" for u in result.units)


def test_parses_javascript_without_crashing() -> None:
    result = parse_source(b"function f() { return 1; }\n", "f.js", Language.javascript)
    assert result is not None
    assert any(u.unit_type == "function" and u.name == "f" for u in result.units)


def test_parses_typescript_without_crashing() -> None:
    result = parse_source(b"export function f(): number { return 1; }\n", "f.ts", Language.typescript)
    assert result is not None
    assert any(u.unit_type == "function" and u.name == "f" for u in result.units)


def test_parses_tsx_without_crashing() -> None:
    source = b"export function F() { return <div>hi</div>; }\n"
    result = parse_source(source, "F.tsx", Language.typescript)
    assert result is not None
    assert any(u.unit_type == "function" and u.name == "F" for u in result.units)
