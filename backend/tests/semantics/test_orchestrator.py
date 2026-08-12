from sqlmodel import select

from app.db.models import AnalysisSnapshot, ModuleSummary, PatternClaim, TradeoffCard
from app.db.session import async_session_factory
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.module_summarizer import ModuleSummaryOutput
from app.semantics.orchestrator import run_layer_b
from app.semantics.pattern_detector import PatternClaimOutput
from app.semantics.tradeoff_extractor import TradeoffCardOutput

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
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(primary_pattern="modular monolith", confidence="medium", evidence=[], caveats=None),
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
                    evidence_refs=[],
                ),
                model="fake",
                stop_reason="end_turn",
                usage={},
            ),
        ]
    )


async def _counts(snapshot_id) -> tuple[int, int, int]:
    async with async_session_factory() as session:
        summaries = list((await session.exec(select(ModuleSummary).where(ModuleSummary.snapshot_id == snapshot_id))).all())
        patterns = list((await session.exec(select(PatternClaim).where(PatternClaim.snapshot_id == snapshot_id))).all())
        cards = list((await session.exec(select(TradeoffCard).where(TradeoffCard.snapshot_id == snapshot_id))).all())
    return len(summaries), len(patterns), len(cards)


async def test_run_layer_b_persists_all_three_tables(layer_a_ready_factory) -> None:
    _repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)

    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
        await run_layer_b(session, _seeded_llm(), snapshot, source_dir)

    assert await _counts(snapshot_id) == (1, 1, 1)


async def test_run_layer_b_is_idempotent_under_redelivery(layer_a_ready_factory) -> None:
    _repo_id, snapshot_id, source_dir = await layer_a_ready_factory(_FILES)

    for _ in range(2):
        async with async_session_factory() as session:
            snapshot = await session.get(AnalysisSnapshot, snapshot_id)
            assert snapshot is not None
            await run_layer_b(session, _seeded_llm(), snapshot, source_dir)

    assert await _counts(snapshot_id) == (1, 1, 1)
