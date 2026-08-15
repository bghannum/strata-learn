from app.analysis.parser import parse_source
from app.ingestion.language_detect import Language


def _units_by_name(units):
    return {u.name: u for u in units}


class TestPython:
    SOURCE = b'''"""Module docstring."""
import os
from typing import List
from . import sibling
from ..pkg import thing
from app.config import settings

@app.get("/health")
async def health() -> dict:
    """Health check."""
    return {}

class Foo:
    """A class."""

    @property
    def bar(self, x: int) -> int:
        return x

    def baz(self):
        pass
'''

    def test_module_docstring(self):
        pf = parse_source(self.SOURCE, "app/main.py", Language.python)
        module = _units_by_name(pf.units)["app/main.py"]
        assert module.unit_type == "module"
        assert module.docstring == "Module docstring."

    def test_decorated_function_includes_decorator_in_signature_and_span(self):
        pf = parse_source(self.SOURCE, "app/main.py", Language.python)
        health = _units_by_name(pf.units)["health"]
        assert health.unit_type == "function"
        assert health.signature == '@app.get("/health")\nasync def health() -> dict'
        assert health.line_start == 8  # includes the decorator line
        assert health.docstring == "Health check."

    def test_class_and_qualified_method_names(self):
        pf = parse_source(self.SOURCE, "app/main.py", Language.python)
        names = _units_by_name(pf.units)
        assert names["Foo"].unit_type == "class"
        assert names["Foo"].docstring == "A class."
        assert "Foo.bar" in names  # decorated method, qualified by class name
        assert "Foo.baz" in names

    def test_import_forms(self):
        pf = parse_source(self.SOURCE, "app/main.py", Language.python)
        imports = {(i.raw, i.kind, i.level) for i in pf.imports}
        assert ("os", "python_absolute", 0) in imports
        assert ("typing", "python_absolute", 0) in imports
        assert (".", "python_relative", 1) in imports  # from . import sibling
        assert ("pkg", "python_relative", 2) in imports  # from ..pkg import thing
        assert ("app.config", "python_absolute", 0) in imports

    def test_from_import_captures_names_for_submodule_disambiguation(self):
        pf = parse_source(b"from app.db import models\n", "app/x.py", Language.python)
        assert pf.imports[0].names == ("models",)

    def test_empty_source_returns_none(self):
        assert parse_source(b"", "empty.py", Language.python) is None
        assert parse_source(b"   \n\n", "empty.py", Language.python) is None


class TestJavaScriptTypeScript:
    JS_SOURCE = b'''import React from "react";
import { useState } from "./hooks";
import * as utils from "../utils";
const fs = require("fs");
const local = require("./local");

/** Adds two numbers. */
function add(a, b) {
  return a + b;
}

const mul = (x, y) => x * y;

/** A widget class. */
class Widget {
  /** renders it */
  render() {
    return null;
  }
}

export function exported() {}
export default class Main {}
'''

    def test_function_declaration_with_jsdoc(self):
        pf = parse_source(self.JS_SOURCE, "src/app.js", Language.javascript)
        add = _units_by_name(pf.units)["add"]
        assert add.unit_type == "function"
        assert add.signature == "function add(a, b)"
        assert add.docstring == "Adds two numbers."

    def test_arrow_function_assigned_to_const(self):
        pf = parse_source(self.JS_SOURCE, "src/app.js", Language.javascript)
        mul = _units_by_name(pf.units)["mul"]
        assert mul.unit_type == "function"
        assert "mul" in mul.signature

    def test_class_and_method(self):
        pf = parse_source(self.JS_SOURCE, "src/app.js", Language.javascript)
        names = _units_by_name(pf.units)
        assert names["Widget"].docstring == "A widget class."
        assert names["Widget.render"].docstring == "renders it"

    def test_export_wrapping_is_unwrapped(self):
        pf = parse_source(self.JS_SOURCE, "src/app.js", Language.javascript)
        names = _units_by_name(pf.units)
        assert "exported" in names
        assert "Main" in names

    def test_import_and_require_both_captured(self):
        pf = parse_source(self.JS_SOURCE, "src/app.js", Language.javascript)
        raws = {i.raw for i in pf.imports}
        assert raws == {"react", "./hooks", "../utils", "fs", "./local"}

    def test_typescript_type_annotations_dont_break_parsing(self):
        source = b'''export function Greet(props: {name: string}): string {
  return props.name;
}

class Service {
  async fetch(): Promise<void> {}
}
'''
        pf = parse_source(source, "src/service.ts", Language.typescript)
        names = _units_by_name(pf.units)
        assert names["Greet"].signature == "export function Greet(props: {name: string}): string"
        assert "Service.fetch" in names

    def test_tsx_jsx_syntax_parses(self):
        source = b'''export function Button({ label }: { label: string }) {
  return <button>{label}</button>;
}
'''
        pf = parse_source(source, "src/Button.tsx", Language.typescript)
        assert "Button" in _units_by_name(pf.units)


class TestPythonCallAndGuardFacts:
    """called_names/has_main_guard back entry-point detection (#12), which used
    to regex raw bytes and so counted any mention of a framework as a use of
    it."""

    def test_called_names_captures_bare_and_dotted_callees(self):
        source = b"import uvicorn\n\napp = FastAPI()\nuvicorn.run(app)\n"
        pf = parse_source(source, "main.py", Language.python)
        assert "FastAPI" in pf.called_names
        assert "uvicorn.run" in pf.called_names

    def test_called_names_excludes_mentions_in_comments_and_strings(self):
        source = b'# calls FastAPI()\nREASON = "instantiates Flask()"\n'
        pf = parse_source(source, "notes.py", Language.python)
        assert pf.called_names == ()

    def test_called_names_excludes_a_docstring_mention(self):
        source = b'"""This module explains how Celery() is configured."""\n\nX = 1\n'
        pf = parse_source(source, "docs.py", Language.python)
        assert pf.called_names == ()

    def test_called_names_finds_calls_nested_in_a_function(self):
        source = b"def create_app():\n    return Flask(__name__)\n"
        pf = parse_source(source, "factory.py", Language.python)
        assert "Flask" in pf.called_names

    def test_called_names_deduplicates_repeated_callees(self):
        source = b"f()\nf()\ng()\n"
        pf = parse_source(source, "repeat.py", Language.python)
        assert pf.called_names == ("f", "g")

    def test_main_guard_detected_at_module_level(self):
        source = b'if __name__ == "__main__":\n    main()\n'
        pf = parse_source(source, "cli.py", Language.python)
        assert pf.has_main_guard is True

    def test_main_guard_in_a_string_is_not_detected(self):
        source = b'SNIPPET = \'if __name__ == "__main__":\'\n'
        pf = parse_source(source, "template.py", Language.python)
        assert pf.has_main_guard is False

    def test_main_guard_nested_in_a_function_is_not_detected(self):
        source = b'def f():\n    if __name__ == "__main__":\n        pass\n'
        pf = parse_source(source, "nested.py", Language.python)
        assert pf.has_main_guard is False

    def test_unrelated_module_level_if_is_not_a_main_guard(self):
        source = b'import sys\n\nif sys.platform == "darwin":\n    pass\n'
        pf = parse_source(source, "platform.py", Language.python)
        assert pf.has_main_guard is False

    def test_javascript_files_have_empty_python_only_facts(self):
        source = b"const app = express();\n"
        pf = parse_source(source, "src/app.js", Language.javascript)
        assert pf.called_names == ()
        assert pf.has_main_guard is False
