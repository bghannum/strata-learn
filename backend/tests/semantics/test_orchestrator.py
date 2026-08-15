from sqlmodel import select

from app.analysis.subsystems import ROOT_KEY
from app.db.models import AnalysisSnapshot, ModuleSummary, PatternClaim, Subsystem, TradeoffCard
from app.db.session import async_session_factory
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.module_summarizer import ModuleSummaryOutput
from app.semantics.orchestrator import run_layer_b
from app.semantics.pattern_detector import PatternClaimOutput, PatternEvidenceItem
from app.semantics.subsystem_namer import SubsystemNameItem, SubsystemNameOutput
from app.semantics.tradeoff_extractor import EvidenceRef, TradeoffCardOutput

_FILES = {"app/worker.py": "import arq\n\n\ndef main():\n    pass\n"}


def _seeded_llm() -> FakeLLMProvider:
    return FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=ModuleSummaryOutput(purpose="p", role_in_system="r", key_concepts=["c"]),
                model="fake",
                stop_reason="end_turn",
                usage={},
            ),
            # Subsystem naming runs between module summaries and pattern
            # detection, so it takes this slot in the scripted queue.
            LLMResponse(
                text="",
                # _FILES is a single file, so app/ is under MIN_SUBSYSTEM_FILES
                # and merges up to the root key — which is what the namer is
                # asked about.
                parsed=SubsystemNameOutput(
                    subsystems=[SubsystemNameItem(key=ROOT_KEY, name="Application", role="does the work")]
                ),
                model="fake",
                stop_reason="end_turn",
                usage={},
            ),
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(
                    primary_pattern="modular monolith",
                    confidence="medium",
                    evidence=[PatternEvidenceItem(claim="single-file repo", supporting_paths=["app/worker.py"])],
                    caveats=None,
                ),
                model="fake",
                stop_reason="end_turn",
                usage={},
            ),
            LLMResponse(
                text="",
                parsed=TradeoffCardOutput(
                    decision="use arq",
                    alternatives_considered=["celery"],
                    likely_reasoning="lighter weight",
                    tradeoff_cost="another moving part",
                    confidence="medium",
                    evidence_refs=[EvidenceRef(file_path="app/worker.py", line_start=1, line_end=5)],
                ),
                model="fake",
                stop_reason="end_turn",
                usage={},
            ),
        ]
    )


async def _counts(snapshot_id) -> tuple[int, int, int, int]:
    async with async_session_factory() as session:
        summaries = list((await session.exec(select(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot_id))).all())
        patterns = list((await session.exec(select(PatternClaim).where(PatternClaim.snapshot_id == snapshot_id))).all())
        cards = list((await session.exec(select(TradeoffCard).where(TradeoffCard.snapshot_id == snapshot_id))).all())
        subsystems = list((await session.exec(select(Subsystem).where(Subsystem.snapshot_id == snapshot_id))).all())
    return len(summaries), len(patterns), len(cards), len(subsystems)


async def test_run_layer_b_persists_all_four_tables(layer_a_ready_factory) -> None:
    _repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)

    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
    await run_layer_b(_seeded_llm(), snapshot, source_dir)

    assert await _counts(snapshot_id) == (1, 1, 1, 1)


async def test_run_layer_b_persists_subsystem_membership_and_order(layer_a_ready_factory) -> None:
    _repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)

    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
    await run_layer_b(_seeded_llm(), snapshot, source_dir)

    async with async_session_factory() as session:
        subsystems = list(
            (await session.exec(select(Subsystem).where(Subsystem.snapshot_id == snapshot_id))).all()
        )

    assert [s.key for s in subsystems] == [ROOT_KEY]
    assert subsystems[0].name == "Application"
    # membership is Layer A ground truth, carried through unchanged
    assert subsystems[0].file_paths == ["app/worker.py"]
    assert subsystems[0].order == 0


async def test_run_layer_b_is_idempotent_under_redelivery(layer_a_ready_factory) -> None:
    _repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)

    for _ in range(2):
        async with async_session_factory() as session:
            snapshot = await session.get(AnalysisSnapshot, snapshot_id)
            assert snapshot is not None
        await run_layer_b(_seeded_llm(), snapshot, source_dir)

    # subsystems included: arq is at-least-once, so a redelivered job must not
    # double every row in the new table either
    assert await _counts(snapshot_id) == (1, 1, 1, 1)
