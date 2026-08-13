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


def chunk_by_module(units: list[CodeUnit]) -> list[ModuleChunk]:
    by_file: dict[str, list[CodeUnit]] = {}
    for unit in units:
        by_file.setdefault(unit.file_path, []).append(unit)

    # Deterministic order when over the cap — the same chunks every time,
    # including on arq redelivery, not whatever order the DB happens to
    # return CodeUnit rows in (a plain SELECT has no ordering guarantee
    # without an explicit ORDER BY).
    chunks: list[ModuleChunk] = []
    for file_path in sorted(by_file):
        if len(chunks) >= MAX_CHUNKS_PER_SNAPSHOT:
            break

        file_units = by_file[file_path]
        module_unit = next((u for u in file_units if u.unit_type == UnitType.module), None)
        if module_unit is None:
            continue  # no module-level unit for this file (shouldn't happen — parser.py always emits one)

        other_units = sorted((u for u in file_units if u is not module_unit), key=lambda u: u.line_start)

        if not other_units:
            chunks.append(ModuleChunk(file_path=file_path, module_unit=module_unit, units=[]))
            continue

        for i in range(0, len(other_units), MAX_UNITS_PER_CHUNK):
            if len(chunks) >= MAX_CHUNKS_PER_SNAPSHOT:
                # Stop mid-file too, not just between files — a single file
                # with enough units could otherwise blow the budget alone.
                break
            chunks.append(
                ModuleChunk(
                    file_path=file_path,
                    module_unit=module_unit,
                    units=other_units[i : i + MAX_UNITS_PER_CHUNK],
                )
            )

    return chunks
