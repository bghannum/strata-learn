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

# A hard cap on how many files get a module summary per snapshot. Each chunk
# is one real, sequential, billed LLM call — but ingestion has no matching
# cap (zip allows up to 5,000 files; git clone has none at all), so an
# unbounded loop could make thousands of paid calls before inevitably hitting
# WorkerSettings.job_timeout anyway, with nothing persisted to show for it
# (found via Codex's Phase 2 pre-push review). This is a stopgap, not a
# scaling solution — bounded concurrency/batching is the real fix once it's
# actually needed (see orchestrator.py's docstring).
MAX_FILES_PER_SNAPSHOT = 200


@dataclass(frozen=True)
class ModuleChunk:
    file_path: str
    module_unit: CodeUnit
    units: list[CodeUnit]


def chunk_by_module(units: list[CodeUnit]) -> list[ModuleChunk]:
    by_file: dict[str, list[CodeUnit]] = {}
    for unit in units:
        by_file.setdefault(unit.file_path, []).append(unit)

    # A deterministic subset when over the cap — the same files every time,
    # including on arq redelivery, not whatever order the DB happens to
    # return CodeUnit rows in (a plain SELECT has no ordering guarantee
    # without an explicit ORDER BY).
    selected_file_paths = sorted(by_file)[:MAX_FILES_PER_SNAPSHOT]

    chunks: list[ModuleChunk] = []
    for file_path in selected_file_paths:
        file_units = by_file[file_path]
        module_unit = next((u for u in file_units if u.unit_type == UnitType.module), None)
        if module_unit is None:
            continue  # no module-level unit for this file (shouldn't happen — parser.py always emits one)

        other_units = sorted((u for u in file_units if u is not module_unit), key=lambda u: u.line_start)

        if not other_units:
            chunks.append(ModuleChunk(file_path=file_path, module_unit=module_unit, units=[]))
            continue

        for i in range(0, len(other_units), MAX_UNITS_PER_CHUNK):
            chunks.append(
                ModuleChunk(
                    file_path=file_path,
                    module_unit=module_unit,
                    units=other_units[i : i + MAX_UNITS_PER_CHUNK],
                )
            )

    return chunks
