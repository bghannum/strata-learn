import uuid

from app.db.models import CodeUnit, UnitType
from app.semantics.chunking import (
    MAX_CHUNKS_PER_SNAPSHOT,
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


def test_chunk_by_module_caps_total_chunks_across_many_files_deterministically() -> None:
    # Found via Codex's Phase 2 pre-push review: ingestion allows far more
    # files than Layer B should ever summarize sequentially (billed LLM
    # calls). The chosen subset must also be deterministic — the same chunks
    # every time, not whatever order the caller happened to pass units in
    # (which, in production, comes from an unordered SELECT).
    file_count = MAX_CHUNKS_PER_SNAPSHOT + 10
    units = [_unit(f"file_{i:03d}.py", UnitType.module, f"file_{i:03d}.py", 1, 5) for i in range(file_count)]

    chunks = chunk_by_module(units)

    assert len(chunks) == MAX_CHUNKS_PER_SNAPSHOT
    selected = {c.file_path for c in chunks}
    # deterministic: alphabetically-first MAX_CHUNKS_PER_SNAPSHOT file names
    expected = {f"file_{i:03d}.py" for i in range(MAX_CHUNKS_PER_SNAPSHOT)}
    assert selected == expected


def test_single_chunk_file_is_part_one_of_one() -> None:
    units = [
        _unit("a.py", UnitType.module, "a.py", 1, 20),
        _unit("a.py", UnitType.function, "helper", 10, 15),
    ]

    chunk = chunk_by_module(units)[0]

    assert (chunk.chunk_index, chunk.chunk_count) == (1, 1)


def test_split_file_chunks_are_numbered_in_order() -> None:
    # #14: every chunk of a file shares its file_path and its whole-module line
    # range, so before this the persisted rows were indistinguishable and a
    # consumer couldn't tell one part from another — or from a whole summary.
    units = [_unit("big.py", UnitType.module, "big.py", 1, 1000)]
    units += [
        _unit("big.py", UnitType.function, f"fn_{i}", i, i + 1) for i in range(MAX_UNITS_PER_CHUNK + 10)
    ]

    chunks = chunk_by_module(units)

    assert [(c.chunk_index, c.chunk_count) for c in chunks] == [(1, 2), (2, 2)]
    # the ordering the index encodes is the unit order, which the shared line
    # range could never express
    assert chunks[0].units[0].name == "fn_0"
    assert chunks[1].units[0].name == f"fn_{MAX_UNITS_PER_CHUNK}"


def test_chunk_count_reflects_chunks_actually_emitted_not_chunks_wanted() -> None:
    # A file cut off mid-way by MAX_CHUNKS_PER_SNAPSHOT must report the count
    # that will really exist — claiming "part 1 of 5" when only 3 summaries are
    # ever generated would describe rows no consumer can find.
    unit_count = (MAX_CHUNKS_PER_SNAPSHOT + 5) * MAX_UNITS_PER_CHUNK
    units = [_unit("huge.py", UnitType.module, "huge.py", 1, 100000)]
    units += [_unit("huge.py", UnitType.function, f"fn_{i}", i, i + 1) for i in range(unit_count)]

    chunks = chunk_by_module(units)

    assert len(chunks) == MAX_CHUNKS_PER_SNAPSHOT
    assert all(c.chunk_count == MAX_CHUNKS_PER_SNAPSHOT for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(1, MAX_CHUNKS_PER_SNAPSHOT + 1))


def test_chunk_indices_are_per_file_not_global() -> None:
    units = []
    for name in ("a.py", "b.py"):
        units.append(_unit(name, UnitType.module, name, 1, 20))
        units.append(_unit(name, UnitType.function, "helper", 10, 15))

    chunks = chunk_by_module(units)

    assert all(c.chunk_index == 1 and c.chunk_count == 1 for c in chunks)


def test_chunk_by_module_caps_total_chunks_within_a_single_oversized_file() -> None:
    # Found via Codex's Phase 2 pre-push review: a cap on distinct *files*
    # alone doesn't bound billed LLM calls — a single valid file with enough
    # units splits into many chunks via MAX_UNITS_PER_CHUNK and could blow
    # the budget by itself. The cap must stop emission mid-file too.
    unit_count = (MAX_CHUNKS_PER_SNAPSHOT + 5) * MAX_UNITS_PER_CHUNK
    units = [_unit("huge.py", UnitType.module, "huge.py", 1, 100000)]
    units += [_unit("huge.py", UnitType.function, f"fn_{i}", i, i + 1) for i in range(unit_count)]

    chunks = chunk_by_module(units)

    assert len(chunks) == MAX_CHUNKS_PER_SNAPSHOT
    assert all(c.file_path == "huge.py" for c in chunks)
