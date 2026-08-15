"""Entry-point detection: heuristics over common patterns (a `__main__` guard,
package.json scripts/main, Dockerfile CMD/ENTRYPOINT, manage.py, FastAPI/Flask/
arq/Celery app objects). LAYER A — structural matching only, no LLM (ADR-006).
False negatives are expected and fine; a heuristic that starts guessing when it
can't point at a concrete source pattern is worse than one that stays quiet.

Python detection reads the tree-sitter facts parser.py already extracted
(`called_names`, `has_main_guard`, and class units) rather than regex-matching
raw file bytes. The regex version counted any *mention* of a framework as a use
of it, so a comment, docstring, or string literal naming `FastAPI(` flagged its
file — including this module, which flagged itself on its own `reason` strings
(#12). Entry points feed the Overview section, the pattern_detector prompt, and
tradeoff_extractor's decision-point scoring, so a false positive there
propagates into three separate generated outputs.

package.json is still parsed as JSON and Dockerfile still matched by regex:
neither is code with an AST to consult, and both are already matched
structurally enough for their format.
"""

import json
import re
from pathlib import Path

from app.analysis.parser import ParsedFile
from app.ingestion.walker import WalkedFile

_DOCKER_CMD = re.compile(rb"^\s*(CMD|ENTRYPOINT)\s+(.*)$", re.MULTILINE)

# Framework objects whose *construction* marks the file as an entry point, keyed
# by the callee name as written in source.
_CONSTRUCTOR_ENTRY_POINTS = {
    "FastAPI": ("http", "instantiates FastAPI()"),
    "Flask": ("http", "instantiates Flask()"),
    "Celery": ("worker", "instantiates Celery()"),
}

# Calls matched on their full dotted name instead of the final attribute. A
# bare "run" is far too common to treat as a signal on its own, whereas the
# constructors above are distinctive enough that `web.FastAPI()` is still
# almost certainly the real thing.
_DOTTED_CALL_ENTRY_POINTS = {
    "uvicorn.run": ("http", "calls uvicorn.run()"),
}

# arq's documented convention is a class by this exact name; the class *body*
# is the worker's configuration, so defining one is the entry point.
_ARQ_WORKER_SETTINGS = "WorkerSettings"


def detect_entry_points(files: list[WalkedFile], parsed_files: list[ParsedFile]) -> list[dict]:
    parsed_by_path = {pf.relative_path: pf for pf in parsed_files}

    entry_points: list[dict] = []
    for f in files:
        name = Path(f.relative_path).name
        if name.endswith(".py"):
            entry_points.extend(_python_entry_points(f, parsed_by_path.get(f.relative_path)))
        elif name == "package.json":
            entry_points.extend(_package_json_entry_points(f))
        elif name == "Dockerfile":
            entry_points.extend(_dockerfile_entry_points(f))
    return entry_points


def _python_entry_points(f: WalkedFile, parsed: ParsedFile | None) -> list[dict]:
    points: list[dict] = []

    # Filename-based, so it holds even for a file tree-sitter produced nothing
    # for (an empty manage.py still is one).
    if Path(f.relative_path).name == "manage.py":
        points.append({"file": f.relative_path, "kind": "cli", "reason": "manage.py (Django management entrypoint)"})

    if parsed is None:
        # Empty file, or one parse_file couldn't read — no structural facts to
        # judge on, and guessing from the raw bytes is the behavior this
        # replaced.
        return points

    if parsed.has_main_guard:
        points.append({"file": f.relative_path, "kind": "cli", "reason": 'if __name__ == "__main__": guard'})

    for called in parsed.called_names:
        final_attribute = called.rsplit(".", 1)[-1]
        if final_attribute in _CONSTRUCTOR_ENTRY_POINTS:
            kind, reason = _CONSTRUCTOR_ENTRY_POINTS[final_attribute]
            points.append({"file": f.relative_path, "kind": kind, "reason": reason})
        if called in _DOTTED_CALL_ENTRY_POINTS:
            kind, reason = _DOTTED_CALL_ENTRY_POINTS[called]
            points.append({"file": f.relative_path, "kind": kind, "reason": reason})

    if any(u.unit_type == "class" and u.name == _ARQ_WORKER_SETTINGS for u in parsed.units):
        points.append({"file": f.relative_path, "kind": "worker", "reason": "defines an arq WorkerSettings class"})

    return points


def _package_json_entry_points(f: WalkedFile) -> list[dict]:
    try:
        data = json.loads(f.path.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    points: list[dict] = []
    main = data.get("main")
    if isinstance(main, str):
        points.append({"file": f.relative_path, "kind": "cli", "reason": f'package.json "main": "{main}"'})

    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for script_name in ("start", "dev", "serve"):
            command = scripts.get(script_name)
            if isinstance(command, str):
                points.append(
                    {
                        "file": f.relative_path,
                        "kind": "http",
                        "reason": f'package.json script "{script_name}": "{command}"',
                    }
                )
    return points


def _dockerfile_entry_points(f: WalkedFile) -> list[dict]:
    try:
        content = f.path.read_bytes()
    except OSError:
        return []

    points: list[dict] = []
    for match in _DOCKER_CMD.finditer(content):
        instruction = match.group(1).decode("ascii")
        command = match.group(2).decode("utf-8", errors="replace").strip()
        points.append(
            {
                "file": f.relative_path,
                "kind": _dockerfile_command_kind(command),
                "reason": f"Dockerfile {instruction} {command}",
            }
        )
    return points


def _dockerfile_command_kind(command: str) -> str:
    lowered = command.lower()
    if any(tool in lowered for tool in ("uvicorn", "gunicorn", "flask run", "npm start", "npm run dev")):
        return "http"
    if any(tool in lowered for tool in ("celery", "arq", "worker")):
        return "worker"
    return "cli"
