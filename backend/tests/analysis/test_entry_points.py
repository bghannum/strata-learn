from pathlib import Path

from app.analysis.entry_points import detect_entry_points
from app.analysis.parser import ParsedFile, parse_file
from app.ingestion.language_detect import detect_language
from app.ingestion.walker import WalkedFile


def _walked(tmp_path: Path, relative_path: str, content: str) -> WalkedFile:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return WalkedFile(path=path, relative_path=relative_path)


def _parse(walked: list[WalkedFile]) -> list[ParsedFile]:
    """Mirrors analyze_source: parse whatever is in v1 language scope, and pass
    the results to detect_entry_points rather than letting it re-read bytes."""
    parsed: list[ParsedFile] = []
    for wf in walked:
        language = detect_language(wf.path)
        if language is None:
            continue
        pf = parse_file(wf.path, wf.relative_path, language)
        if pf is not None:
            parsed.append(pf)
    return parsed


def _detect(walked: list[WalkedFile]) -> list[dict]:
    return detect_entry_points(walked, _parse(walked))


def _kinds(entry_points: list[dict], file: str) -> set[str]:
    return {e["kind"] for e in entry_points if e["file"] == file}


def test_main_guard_detected(tmp_path: Path) -> None:
    f = _walked(tmp_path, "cli.py", 'if __name__ == "__main__":\n    pass\n')
    assert "cli" in _kinds(_detect([f]), "cli.py")


def test_fastapi_app_detected_as_http(tmp_path: Path) -> None:
    f = _walked(tmp_path, "app/main.py", "app = FastAPI()\n")
    assert "http" in _kinds(_detect([f]), "app/main.py")


def test_flask_app_detected_as_http(tmp_path: Path) -> None:
    f = _walked(tmp_path, "app.py", "app = Flask(__name__)\n")
    assert "http" in _kinds(_detect([f]), "app.py")


def test_uvicorn_run_detected_as_http(tmp_path: Path) -> None:
    f = _walked(tmp_path, "serve.py", "import uvicorn\nuvicorn.run(app)\n")
    assert "http" in _kinds(_detect([f]), "serve.py")


def test_arq_worker_settings_detected_as_worker(tmp_path: Path) -> None:
    f = _walked(tmp_path, "worker/tasks.py", "class WorkerSettings:\n    pass\n")
    assert "worker" in _kinds(_detect([f]), "worker/tasks.py")


def test_celery_app_detected_as_worker(tmp_path: Path) -> None:
    f = _walked(tmp_path, "worker.py", "app = Celery('proj')\n")
    assert "worker" in _kinds(_detect([f]), "worker.py")


def test_manage_py_detected_as_cli(tmp_path: Path) -> None:
    f = _walked(tmp_path, "manage.py", "")
    assert "cli" in _kinds(_detect([f]), "manage.py")


def test_package_json_main_and_scripts(tmp_path: Path) -> None:
    content = '{"main": "index.js", "scripts": {"start": "node index.js", "test": "jest"}}'
    f = _walked(tmp_path, "package.json", content)
    reasons = " ".join(e["reason"] for e in _detect([f]) if e["file"] == "package.json")
    assert '"main"' in reasons
    assert "start" in reasons
    assert "test" not in reasons  # "test" script is deliberately not treated as an entrypoint


def test_dockerfile_uvicorn_cmd_detected_as_http(tmp_path: Path) -> None:
    f = _walked(tmp_path, "Dockerfile", 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]\n')
    assert "http" in _kinds(_detect([f]), "Dockerfile")


def test_dockerfile_celery_cmd_detected_as_worker(tmp_path: Path) -> None:
    f = _walked(tmp_path, "Dockerfile", 'CMD ["celery", "-A", "proj", "worker"]\n')
    assert "worker" in _kinds(_detect([f]), "Dockerfile")


def test_dockerfile_plain_command_defaults_to_cli(tmp_path: Path) -> None:
    f = _walked(tmp_path, "Dockerfile", 'CMD ["python", "manage.py", "migrate"]\n')
    assert "cli" in _kinds(_detect([f]), "Dockerfile")


def test_plain_module_has_no_entry_points(tmp_path: Path) -> None:
    f = _walked(tmp_path, "utils.py", "def helper():\n    return 1\n")
    assert _detect([f]) == []


# --- #12: a mention is not a use ---


def test_framework_named_in_comment_is_not_an_entry_point(tmp_path: Path) -> None:
    f = _walked(tmp_path, "notes.py", "# this module does not call FastAPI() or Flask()\nx = 1\n")
    assert _detect([f]) == []


def test_framework_named_in_docstring_is_not_an_entry_point(tmp_path: Path) -> None:
    source = '"""Explains how the app instantiates FastAPI() at startup."""\n\nVALUE = 1\n'
    f = _walked(tmp_path, "docs.py", source)
    assert _detect([f]) == []


def test_framework_named_in_string_literal_is_not_an_entry_point(tmp_path: Path) -> None:
    f = _walked(tmp_path, "reasons.py", 'REASON = "instantiates FastAPI()"\nOTHER = "calls uvicorn.run()"\n')
    assert _detect([f]) == []


def test_main_guard_in_string_literal_is_not_an_entry_point(tmp_path: Path) -> None:
    f = _walked(tmp_path, "template.py", 'SNIPPET = \'if __name__ == "__main__":\'\n')
    assert _detect([f]) == []


def test_main_guard_nested_in_function_is_not_an_entry_point(tmp_path: Path) -> None:
    # Only a module-level guard runs on import; one inside a function body is
    # dead weight that never makes the file a script entry point.
    source = 'def f():\n    if __name__ == "__main__":\n        pass\n'
    f = _walked(tmp_path, "nested.py", source)
    assert _detect([f]) == []


def test_imported_worker_settings_is_not_an_entry_point(tmp_path: Path) -> None:
    # Referencing arq's WorkerSettings (importing it, passing it to something)
    # is not the same as defining the class that configures this repo's worker.
    source = "from app.worker.tasks import WorkerSettings\n\nsettings = WorkerSettings\n"
    f = _walked(tmp_path, "runner.py", source)
    assert _detect([f]) == []


def test_worker_settings_as_a_function_is_not_an_entry_point(tmp_path: Path) -> None:
    source = "def WorkerSettings():\n    return None\n"
    f = _walked(tmp_path, "shim.py", source)
    assert _detect([f]) == []


def test_app_factory_is_still_detected(tmp_path: Path) -> None:
    # The flip side of the fix: scanning only module level would miss the very
    # common factory pattern, so a real call inside a function still counts.
    source = "def create_app():\n    app = Flask(__name__)\n    return app\n"
    f = _walked(tmp_path, "factory.py", source)
    assert "http" in _kinds(_detect([f]), "factory.py")


def test_attribute_qualified_constructor_is_detected(tmp_path: Path) -> None:
    source = "import fastapi\n\napp = fastapi.FastAPI()\n"
    f = _walked(tmp_path, "qualified.py", source)
    assert "http" in _kinds(_detect([f]), "qualified.py")


def test_unrelated_run_call_is_not_an_entry_point(tmp_path: Path) -> None:
    # uvicorn.run matches on its full dotted name — a bare `run()` or some
    # other module's `.run()` is far too common to treat as a signal.
    f = _walked(tmp_path, "job.py", "import scheduler\n\nscheduler.run(task)\nrun()\n")
    assert _detect([f]) == []


def test_entry_points_module_does_not_flag_itself() -> None:
    """The dogfooding case from #12: this module's own `reason` strings contain
    the literal text of every pattern it looks for, so the regex version
    reported it as an http *and* worker entry point of this repo."""
    source_path = Path(__file__).resolve().parents[2] / "app" / "analysis" / "entry_points.py"
    assert source_path.is_file(), source_path

    walked = [WalkedFile(path=source_path, relative_path="app/analysis/entry_points.py")]
    assert _detect(walked) == []
