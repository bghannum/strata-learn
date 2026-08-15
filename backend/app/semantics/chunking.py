"""Groups a snapshot's CodeUnit rows into LLM-sized chunks by tree-sitter unit
(one chunk per file: its module unit plus class/function units), not by token
windows — a token-window split could cut a class definition mid-body.
"""

from dataclasses import dataclass

from app.db.models import CodeUnit, UnitType

# A unit-count heuristic (not a token count), matching the "not token windows"
# requirement. A file with more class/function units than this is split into
# multiple sub-chunks, each still carrying the module_unit for context.
MAX_UNITS_PER_CHUNK = 60

# A hard cap on the TOTAL number of chunks emitted per snapshot — each chunk
# is one real, sequential, billed LLM call, so this is what actually bounds
# call count, unlike a per-file cap (found via Codex's Phase 2 pre-push
# review: a first version of this capped distinct *files*, but a single
# file with enough units still splits into many chunks via
# MAX_UNITS_PER_CHUNK, so a file count cap alone doesn't bound calls — a
# valid sub-1MiB file with thousands of tiny one-line functions could still
# blow the budget by itself). Ingestion has no matching cap of its own (zip
# allows up to 5,000 files; git clone has none at all), so without this an
# unbounded loop could make thousands of paid calls before inevitably
# hitting WorkerSettings.job_timeout anyway, with nothing persisted to show
# for it. This is a stopgap, not a scaling solution — bounded concurrency/
# batching is the real fix once it's actually needed (see orchestrator.py's
# docstring).
MAX_CHUNKS_PER_SNAPSHOT = 200


@dataclass(frozen=True)
class ModuleChunk:
    file_path: str
    module_unit: CodeUnit
    units: list[CodeUnit]
    # Position of this chunk among the chunks actually emitted for its file, and
    # how many there are. Both are 1 for the overwhelmingly common single-chunk
    # file. Carried through to the persisted ModuleSummary (#14) so a consumer
    # asking "the summary for file X" can tell a complete summary from one of N
    # partial ones — every chunk of a file otherwise shares the same file_path
    # and the same whole-module line range, making the rows indistinguishable.
    chunk_index: int = 1
    chunk_count: int = 1


def chunk_by_module(units: list[CodeUnit]) -> list[ModuleChunk]:
    by_file: dict[str, list[CodeUnit]] = {}
    for unit in units:
        by_file.setdefault(unit.file_path, []).append(unit)

    # Deterministic order when over the cap — the same chunks every time,
    # including on arq redelivery, not whatever order the DB happens to
    # return CodeUnit rows in (a plain SELECT has no ordering guarantee
    # without an explicit ORDER BY).
    #
    # Built as (file_path, module_unit, units) triples first and only turned
    # into ModuleChunks once the MAX_CHUNKS_PER_SNAPSHOT cap has been applied:
    # chunk_count has to describe what was actually emitted, so a file cut off
    # mid-way by the global cap reports "part 1 of 2" rather than claiming a
    # part 3 that no summary will ever exist for.
    raw: list[tuple[str, CodeUnit, list[CodeUnit]]] = []
    for file_path in sorted(by_file):
        if len(raw) >= MAX_CHUNKS_PER_SNAPSHOT:
            break

        file_units = by_file[file_path]
        module_unit = next((u for u in file_units if u.unit_type == UnitType.module), None)
        if module_unit is None:
            continue  # no module-level unit for this file (shouldn't happen — parser.py always emits one)

        other_units = sorted((u for u in file_units if u is not module_unit), key=lambda u: u.line_start)

        if not other_units:
            raw.append((file_path, module_unit, []))
            continue

        for i in range(0, len(other_units), MAX_UNITS_PER_CHUNK):
            if len(raw) >= MAX_CHUNKS_PER_SNAPSHOT:
                # Stop mid-file too, not just between files — a single file
                # with enough units could otherwise blow the budget alone.
                break
            raw.append((file_path, module_unit, other_units[i : i + MAX_UNITS_PER_CHUNK]))

    counts: dict[str, int] = {}
    for file_path, _module_unit, _chunk_units in raw:
        counts[file_path] = counts.get(file_path, 0) + 1

    chunks: list[ModuleChunk] = []
    seen: dict[str, int] = {}
    for file_path, module_unit, chunk_units in raw:
        seen[file_path] = seen.get(file_path, 0) + 1
        chunks.append(
            ModuleChunk(
                file_path=file_path,
                module_unit=module_unit,
                units=chunk_units,
                chunk_index=seen[file_path],
                chunk_count=counts[file_path],
            )
        )
    return chunks
