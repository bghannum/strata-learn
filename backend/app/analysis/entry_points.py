"""Entry-point detection: heuristics over common patterns (a `__main__` guard,
package.json scripts/main, Dockerfile CMD/ENTRYPOINT, manage.py, FastAPI/Flask/
arq/Celery app objects). LAYER A — pattern matching only, no LLM (ADR-006).
False negatives are expected and fine; a heuristic that starts guessing when it
can't point at a concrete source pattern is worse than one that stays quiet.
"""

import json
import re
from pathlib import Path

from app.ingestion.walker import WalkedFile

_PY_MAIN_GUARD = re.compile(rb"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:")
_FASTAPI_APP = re.compile(rb"\bFastAPI\s*\(")
_FLASK_APP = re.compile(rb"\bFlask\s*\(")
_UVICORN_RUN = re.compile(rb"\buvicorn\.run\s*\(")
_ARQ_WORKER = re.compile(rb"\bWorkerSettings\b")
_CELERY_APP = re.compile(rb"\bCelery\s*\(")
_DOCKER_CMD = re.compile(rb"^\s*(CMD|ENTRYPOINT)\s+(.*)$", re.MULTILINE)


def detect_entry_points(files: list[WalkedFile]) -> list[dict]:
    entry_points: list[dict] = []
    for f in files:
        name = Path(f.relative_path).name
        if name.endswith(".py"):
            entry_points.extend(_python_entry_points(f))
        elif name == "package.json":
            entry_points.extend(_package_json_entry_points(f))
        elif name == "Dockerfile":
            entry_points.extend(_dockerfile_entry_points(f))
    return entry_points


def _python_entry_points(f: WalkedFile) -> list[dict]:
    try:
        content = f.path.read_bytes()
    except OSError:
        return []

    points: list[dict] = []
    if Path(f.relative_path).name == "manage.py":
        points.append({"file": f.relative_path, "kind": "cli", "reason": "manage.py (Django management entrypoint)"})
    if _PY_MAIN_GUARD.search(content):
        points.append({"file": f.relative_path, "kind": "cli", "reason": 'if __name__ == "__main__": guard'})
    if _FASTAPI_APP.search(content):
        points.append({"file": f.relative_path, "kind": "http", "reason": "instantiates FastAPI()"})
    if _FLASK_APP.search(content):
        points.append({"file": f.relative_path, "kind": "http", "reason": "instantiates Flask()"})
    if _UVICORN_RUN.search(content):
        points.append({"file": f.relative_path, "kind": "http", "reason": "calls uvicorn.run()"})
    if _ARQ_WORKER.search(content):
        points.append({"file": f.relative_path, "kind": "worker", "reason": "defines an arq WorkerSettings class"})
    if _CELERY_APP.search(content):
        points.append({"file": f.relative_path, "kind": "worker", "reason": "instantiates Celery()"})
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
