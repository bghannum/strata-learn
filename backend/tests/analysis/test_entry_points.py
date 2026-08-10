from pathlib import Path

from app.analysis.entry_points import detect_entry_points
from app.ingestion.walker import WalkedFile


def _walked(tmp_path: Path, relative_path: str, content: str) -> WalkedFile:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return WalkedFile(path=path, relative_path=relative_path)


def _kinds(entry_points: list[dict], file: str) -> set[str]:
    return {e["kind"] for e in entry_points if e["file"] == file}


def test_main_guard_detected(tmp_path: Path) -> None:
    f = _walked(tmp_path, "cli.py", 'if __name__ == "__main__":\n    pass\n')
    points = detect_entry_points([f])
    assert "cli" in _kinds(points, "cli.py")


def test_fastapi_app_detected_as_http(tmp_path: Path) -> None:
    f = _walked(tmp_path, "app/main.py", "app = FastAPI()\n")
    points = detect_entry_points([f])
    assert "http" in _kinds(points, "app/main.py")


def test_flask_app_detected_as_http(tmp_path: Path) -> None:
    f = _walked(tmp_path, "app.py", "app = Flask(__name__)\n")
    points = detect_entry_points([f])
    assert "http" in _kinds(points, "app.py")


def test_arq_worker_settings_detected_as_worker(tmp_path: Path) -> None:
    f = _walked(tmp_path, "worker/tasks.py", "class WorkerSettings:\n    pass\n")
    points = detect_entry_points([f])
    assert "worker" in _kinds(points, "worker/tasks.py")


def test_celery_app_detected_as_worker(tmp_path: Path) -> None:
    f = _walked(tmp_path, "worker.py", "app = Celery('proj')\n")
    points = detect_entry_points([f])
    assert "worker" in _kinds(points, "worker.py")


def test_manage_py_detected_as_cli(tmp_path: Path) -> None:
    f = _walked(tmp_path, "manage.py", "")
    points = detect_entry_points([f])
    assert "cli" in _kinds(points, "manage.py")


def test_package_json_main_and_scripts(tmp_path: Path) -> None:
    content = '{"main": "index.js", "scripts": {"start": "node index.js", "test": "jest"}}'
    f = _walked(tmp_path, "package.json", content)
    points = detect_entry_points([f])
    reasons = " ".join(e["reason"] for e in points if e["file"] == "package.json")
    assert '"main"' in reasons
    assert "start" in reasons
    assert "test" not in reasons  # "test" script is deliberately not treated as an entrypoint


def test_dockerfile_uvicorn_cmd_detected_as_http(tmp_path: Path) -> None:
    content = 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]\n'
    f = _walked(tmp_path, "Dockerfile", content)
    points = detect_entry_points([f])
    assert "http" in _kinds(points, "Dockerfile")


def test_dockerfile_celery_cmd_detected_as_worker(tmp_path: Path) -> None:
    content = 'CMD ["celery", "-A", "proj", "worker"]\n'
    f = _walked(tmp_path, "Dockerfile", content)
    points = detect_entry_points([f])
    assert "worker" in _kinds(points, "Dockerfile")


def test_dockerfile_plain_command_defaults_to_cli(tmp_path: Path) -> None:
    content = 'CMD ["python", "manage.py", "migrate"]\n'
    f = _walked(tmp_path, "Dockerfile", content)
    points = detect_entry_points([f])
    assert "cli" in _kinds(points, "Dockerfile")


def test_plain_module_has_no_entry_points(tmp_path: Path) -> None:
    f = _walked(tmp_path, "utils.py", "def helper():\n    return 1\n")
    assert detect_entry_points([f]) == []
