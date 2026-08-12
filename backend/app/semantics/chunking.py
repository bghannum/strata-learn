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


@dataclass(frozen=True)
class ModuleChunk:
    file_path: str
    module_unit: CodeUnit
    units: list[CodeUnit]


def chunk_by_module(units: list[CodeUnit]) -> list[ModuleChunk]:
    by_file: dict[str, list[CodeUnit]] = {}
    for unit in units:
        by_file.setdefault(unit.file_path, []).append(unit)

    chunks: list[ModuleChunk] = []
    for file_path, file_units in by_file.items():
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
