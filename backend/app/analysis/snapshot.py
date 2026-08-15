"""Assembles a full structural analysis of a prepared source directory (walk →
language-detect → parse → dependency graph → entry points), then persists it
as an AnalysisSnapshot + CodeUnit records. LAYER A orchestration — no LLM
calls anywhere in this module (ADR-006); Phase 2 adds a *separate* semantic
pass on top of what's persisted here, never mixed into it.
"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from sqlmodel import delete
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
    # Takes the parsed files too, not just the walked ones: Python entry points
    # are read off tree-sitter facts from the parse above rather than re-derived
    # from raw bytes (#12). No second parse — a file already parsed once here is
    # matched back by relative_path.
    entry_points = detect_entry_points(walked, parsed_files)

    return StructuralAnalysis(
        file_count=len(walked),
        language_summary=language_counts,
        dependency_graph=dependency_graph,
        entry_points=entry_points,
        parsed_files=parsed_files,
    )


async def create_pending_snapshot(session: AsyncSession, repo_id: UUID) -> AnalysisSnapshot:
    """Phase 1.5: the API creates this synchronously, before enqueueing the
    indexing job, so `POST /repos` has a snapshot id to return immediately.
    `complete_snapshot`/`fail_snapshot` fill in the rest once the worker runs."""
    snapshot = AnalysisSnapshot(repo_id=repo_id, status=SnapshotStatus.pending)
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def set_snapshot_status(session: AsyncSession, snapshot_id: UUID, status: SnapshotStatus) -> None:
    snapshot = await session.get(AnalysisSnapshot, snapshot_id)
    if snapshot is None:
        return  # repo/snapshot deleted out from under an in-flight job — nothing to update
    snapshot.status = status
    session.add(snapshot)
    await session.commit()


async def complete_snapshot(
    session: AsyncSession,
    snapshot_id: UUID,
    commit_hash: str | None,
    analysis: StructuralAnalysis,
    final_status: SnapshotStatus = SnapshotStatus.ready,
) -> AnalysisSnapshot | None:
    """Fills in a pending snapshot with analysis results and marks it
    `final_status` (defaults to `ready`, i.e. Layer A is the whole pipeline).
    Run by the worker (worker/pipeline.py) after analyze_source succeeds.

    Phase 2 passes `final_status=SnapshotStatus.analyzing` here specifically
    so this is the *only* commit — without a caller-supplied override, this
    function's own commit would briefly make `ready` externally visible (to
    a poller or a WS client connecting in the gap) before pipeline.py's own
    follow-up transition to `analyzing`, and a WS client that reads `ready`
    disconnects immediately, never seeing Layer B run or fail (found via
    Codex's Phase 2 pre-push review). This function stays Layer-A-only in
    spirit — it doesn't know Layer B exists, it just writes whatever
    terminal-or-not status its caller decides is accurate right now.

    Returns None if the snapshot is gone by the time the job finishes (repo
    deleted mid-index, or — in dev/test — an orphaned job outliving whatever
    created its target row). Matches set_snapshot_status's tolerance above:
    a vanished target is "nothing to do", not a crash-worthy error."""
    snapshot = await session.get(AnalysisSnapshot, snapshot_id)
    if snapshot is None:
        return None

    snapshot.commit_hash = commit_hash
    snapshot.status = final_status
    snapshot.file_count = analysis.file_count
    snapshot.language_summary = analysis.language_summary
    snapshot.dependency_graph = analysis.dependency_graph
    snapshot.entry_points = analysis.entry_points
    session.add(snapshot)
    await session.flush()  # snapshot.id already exists (pending row) — flush just applies the field updates

    # arq is at-least-once, not exactly-once — a redelivered job (worker
    # crash/restart between this commit and arq's own ack bookkeeping) would
    # otherwise re-insert every CodeUnit on top of what a prior attempt
    # already wrote. Clearing first makes this idempotent: redelivery
    # produces the same end state, not duplicates.
    await session.exec(delete(CodeUnit).where(CodeUnit.snapshot_id == snapshot.id))

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


async def fail_snapshot(session: AsyncSession, snapshot_id: UUID) -> None:
    """Run by the worker when the pipeline can't complete (bad source, parse
    crash, etc.). No persisted error detail yet — AnalysisSnapshot has no
    failure-reason column; the WS progress message carries it transiently
    instead (see worker/pipeline.py). Revisit if failures need to be
    diagnosable after the fact, not just at the moment they happen."""
    await set_snapshot_status(session, snapshot_id, SnapshotStatus.failed)
