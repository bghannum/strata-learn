from pathlib import Path

from sqlmodel import select

from app.analysis.snapshot import analyze_source, complete_snapshot
from app.db.models import CodeUnit, SourceType
from app.db.session import async_session_factory


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_analyze_source_end_to_end(tmp_path: Path) -> None:
    # app/__init__.py is deliberately left empty — the walker drops zero-byte
    # files entirely (tests/ingestion/test_walker.py::test_skips_empty_files),
    # so it never reaches language detection or the dependency graph.
    _write(tmp_path, "app/__init__.py", "")
    _write(
        tmp_path,
        "app/main.py",
        "from fastapi import FastAPI\nfrom app.config import settings\n\napp = FastAPI()\n",
    )
    _write(tmp_path, "app/config.py", "import os\n\nsettings = {}\n")
    _write(tmp_path, "README.md", "# not code\n")
    _write(tmp_path, ".gitignore", "*.log\n")
    _write(tmp_path, "debug.log", "noise\n")

    result = analyze_source(tmp_path)

    # file_count counts every file that survives the walk, regardless of
    # language (README.md and .gitignore included; empty __init__.py and the
    # gitignored .log are excluded before this point)
    assert result.file_count == 4  # app/main.py, app/config.py, README.md, .gitignore
    assert result.language_summary == {"python": 2}

    node_ids = {n["id"] for n in result.dependency_graph["nodes"] if n["kind"] == "file"}
    assert node_ids == {"app/main.py", "app/config.py"}

    edges = {(e["source"], e["target"], e["kind"]) for e in result.dependency_graph["edges"]}
    assert ("app/main.py", "app/config.py", "imports") in edges
    assert ("app/main.py", "external:fastapi", "imports_external") in edges

    entry_files = {e["file"] for e in result.entry_points}
    assert "app/main.py" in entry_files


def test_analyze_source_handles_empty_repo(tmp_path: Path) -> None:
    result = analyze_source(tmp_path)
    assert result.file_count == 0
    assert result.language_summary == {}
    assert result.dependency_graph == {"nodes": [], "edges": []}
    assert result.entry_points == []


async def test_complete_snapshot_is_idempotent_under_redelivery(tmp_path: Path, pending_repo_factory) -> None:
    # arq is at-least-once, not exactly-once — a worker crash/restart between
    # complete_snapshot's commit and arq's own ack bookkeeping can redeliver
    # the same job. A second call for the same snapshot must not duplicate
    # CodeUnit rows on top of what the first call already wrote.
    _write(tmp_path, "app.py", "def hello():\n    return 'hi'\n")
    analysis = analyze_source(tmp_path)

    _, snapshot_id = await pending_repo_factory(SourceType.git_url, "irrelevant-for-this-test")

    async with async_session_factory() as session:
        await complete_snapshot(session, snapshot_id, "abc123", analysis)
    async with async_session_factory() as session:
        await complete_snapshot(session, snapshot_id, "abc123", analysis)  # simulated redelivery

    async with async_session_factory() as session:
        result = await session.exec(select(CodeUnit).where(CodeUnit.snapshot_id == snapshot_id))
        units = result.all()

    # One module-level unit + one function-level unit ("hello") per parsed
    # file — confirmed empirically, not assumed. The real assertion here is
    # that it's still 2 after the second (simulated-redelivery) call, not 4.
    assert len(units) == 2
