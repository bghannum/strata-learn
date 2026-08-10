"""Assembles a full structural analysis of a prepared source directory (walk →
language-detect → parse → dependency graph → entry points), then persists it
as an AnalysisSnapshot + CodeUnit records. LAYER A orchestration — no LLM
calls anywhere in this module (ADR-006); Phase 2 adds a *separate* semantic
pass on top of what's persisted here, never mixed into it.
"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from app.analysis.dependency_graph import FileInfo, build_dependency_graph
from app.analysis.entry_points import detect_entry_points
from app.analysis.parser import ParsedFile, parse_file
from app.db.models import AnalysisSnapshot, CodeUnit, SnapshotStatus, UnitType
from app.ingestion.language_detect import detect_language
from app.ingestion.walker import walk_files


@dataclass
class StructuralAnalysis:
    file_count: int
    language_summary: dict[str, int]
    dependency_graph: dict
    entry_points: list[dict]
    parsed_files: list[ParsedFile] = field(default_factory=list)


def analyze_source(source_dir: Path) -> StructuralAnalysis:
    """Pure Layer A analysis — no DB access, no side effects beyond reading
    `source_dir`. Kept separate from persistence so the analysis itself stays
    trivially testable against a plain directory."""
    walked = walk_files(source_dir)

    files: list[FileInfo] = []
    parsed_files: list[ParsedFile] = []
    language_counts: dict[str, int] = {}

    for wf in walked:
        language = detect_language(wf.path)
        if language is None:
            continue  # walked but out of v1 language scope (D10) — still counted in file_count below

        files.append(FileInfo(relative_path=wf.relative_path, language=language))
        language_counts[language.value] = language_counts.get(language.value, 0) + 1

        parsed = parse_file(wf.path, wf.relative_path, language)
        if parsed is not None:
            parsed_files.append(parsed)

    dependency_graph = build_dependency_graph(parsed_files, files)
    entry_points = detect_entry_points(walked)

    return StructuralAnalysis(
        file_count=len(walked),
        language_summary=language_counts,
        dependency_graph=dependency_graph,
        entry_points=entry_points,
        parsed_files=parsed_files,
    )


async def persist_snapshot(
    session: AsyncSession, repo_id: UUID, commit_hash: str | None, analysis: StructuralAnalysis
) -> AnalysisSnapshot:
    snapshot = AnalysisSnapshot(
        repo_id=repo_id,
        commit_hash=commit_hash,
        status=SnapshotStatus.ready,
        file_count=analysis.file_count,
        language_summary=analysis.language_summary,
        dependency_graph=analysis.dependency_graph,
        entry_points=analysis.entry_points,
    )
    session.add(snapshot)
    await session.flush()  # assigns snapshot.id, needed for CodeUnit.snapshot_id below

    for pf in analysis.parsed_files:
        for unit in pf.units:
            session.add(
                CodeUnit(
                    snapshot_id=snapshot.id,
                    file_path=pf.relative_path,
                    unit_type=UnitType(unit.unit_type),
                    name=unit.name,
                    line_start=unit.line_start,
                    line_end=unit.line_end,
                    signature=unit.signature,
                    docstring=unit.docstring,
                )
            )

    await session.commit()
    await session.refresh(snapshot)
    return snapshot
