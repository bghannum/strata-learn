import uuid

from app.db.models import CodeUnit, UnitType
from app.semantics.chunking import (
    MAX_FILES_PER_SNAPSHOT,
    MAX_UNITS_PER_CHUNK,
    chunk_by_module,
)


def _unit(file_path: str, unit_type: UnitType, name: str, line_start: int, line_end: int) -> CodeUnit:
    return CodeUnit(
        snapshot_id=uuid.uuid4(),
        file_path=file_path,
        unit_type=unit_type,
        name=name,
        line_start=line_start,
        line_end=line_end,
    )


def test_chunk_by_module_groups_one_chunk_per_file() -> None:
    units = [
        _unit("a.py", UnitType.module, "a.py", 1, 20),
        _unit("a.py", UnitType.function, "helper", 10, 15),
        _unit("a.py", UnitType.class_, "Thing", 1, 8),
        _unit("b.py", UnitType.module, "b.py", 1, 5),
    ]

    chunks = chunk_by_module(units)

    assert {c.file_path for c in chunks} == {"a.py", "b.py"}
    a_chunk = next(c for c in chunks if c.file_path == "a.py")
    assert a_chunk.module_unit.name == "a.py"
    # line-ordered: Thing (line_start=1) before helper (line_start=10)
    assert [u.name for u in a_chunk.units] == ["Thing", "helper"]

    b_chunk = next(c for c in chunks if c.file_path == "b.py")
    assert b_chunk.units == []


def test_chunk_by_module_skips_file_with_no_module_unit() -> None:
    units = [_unit("orphan.py", UnitType.function, "f", 1, 5)]
    assert chunk_by_module(units) == []


def test_chunk_by_module_splits_oversized_file() -> None:
    units = [_unit("big.py", UnitType.module, "big.py", 1, 1000)]
    units += [
        _unit("big.py", UnitType.function, f"fn_{i}", i, i + 1) for i in range(MAX_UNITS_PER_CHUNK + 10)
    ]

    chunks = chunk_by_module(units)

    assert len(chunks) == 2
    assert all(c.file_path == "big.py" for c in chunks)
    assert all(c.module_unit.name == "big.py" for c in chunks)
    assert len(chunks[0].units) == MAX_UNITS_PER_CHUNK
    assert len(chunks[1].units) == 10


def test_chunk_by_module_caps_total_files_deterministically() -> None:
    # Found via Codex's Phase 2 pre-push review: ingestion allows far more
    # files than Layer B should ever summarize sequentially (billed LLM
    # calls). The chosen subset must also be deterministic — the same files
    # every time, not whatever order the caller happened to pass units in
    # (which, in production, comes from an unordered SELECT).
    file_count = MAX_FILES_PER_SNAPSHOT + 10
    units = [_unit(f"file_{i:03d}.py", UnitType.module, f"file_{i:03d}.py", 1, 5) for i in range(file_count)]

    chunks = chunk_by_module(units)

    assert len(chunks) == MAX_FILES_PER_SNAPSHOT
    selected = {c.file_path for c in chunks}
    # deterministic: alphabetically-first MAX_FILES_PER_SNAPSHOT file names
    expected = {f"file_{i:03d}.py" for i in range(MAX_FILES_PER_SNAPSHOT)}
    assert selected == expected
