import uuid

from app.db.models import CodeUnit, Confidence, PatternClaim, Subsystem, TradeoffCard, UnitType
from app.generation.architecture_narrative import (
    MAX_FILES_PER_SUBSYSTEM_IN_PROMPT,
    MAX_TRADEOFF_CARDS_IN_PROMPT,
    ArchitectureNarrativeOutput,
    WhySection,
    build_architecture_narrative,
)
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse

_SNAPSHOT_ID = uuid.uuid4()


def _module_unit(file_path: str, line_end: int = 40) -> CodeUnit:
    return CodeUnit(
        snapshot_id=_SNAPSHOT_ID,
        file_path=file_path,
        unit_type=UnitType.module,
        name=file_path,
        line_start=1,
        line_end=line_end,
    )


def _pattern_claim() -> PatternClaim:
    return PatternClaim(
        snapshot_id=_SNAPSHOT_ID,
        primary_pattern="modular monolith",
        confidence=Confidence.medium,
        evidence=[{"claim": "one app package", "supporting_paths": ["app/main.py"]}],
        caveats="the worker is nearly its own service",
        prompt_version="v1",
        model="fake-model",
    )


def _subsystem(key: str, name: str, file_paths: list[str], order: int = 0) -> Subsystem:
    return Subsystem(
        snapshot_id=_SNAPSHOT_ID,
        key=key,
        name=name,
        role=f"the {name} part",
        file_paths=file_paths,
        depth=0,
        order=order,
        prompt_version="v1",
        model="fake-model",
    )


def _tradeoff_card(decision: str) -> TradeoffCard:
    return TradeoffCard(
        snapshot_id=_SNAPSHOT_ID,
        decision=decision,
        alternatives_considered=["do it inline"],
        likely_reasoning="indexing takes minutes",
        tradeoff_cost="another moving part to operate",
        confidence=Confidence.medium,
        evidence_refs=[{"file_path": "app/worker.py", "line_start": 1, "line_end": 10}],
        prompt_version="v1",
        model="fake-model",
    )


def _llm(output: ArchitectureNarrativeOutput) -> FakeLLMProvider:
    return FakeLLMProvider(
        [LLMResponse(text="", parsed=output, model="fake-model", stop_reason="end_turn", usage={})]
    )


async def test_returns_none_with_nothing_to_synthesize_from() -> None:
    # No pattern, no subsystems, no trade-offs — spending the strongest model
    # tier on an empty prompt would only buy confident prose about nothing.
    llm = _llm(ArchitectureNarrativeOutput(overview="should not be asked for"))

    assert await build_architecture_narrative(llm, None, [], [], [], []) is None
    assert llm.calls == []


async def test_builds_overview_and_why_sections() -> None:
    llm = _llm(
        ArchitectureNarrativeOutput(
            overview="Requests come in over HTTP and slow work is handed to a worker.",
            why_sections=[
                WhySection(
                    heading="Why indexing runs in a worker",
                    body="Indexing takes minutes; an HTTP request can't wait that long.",
                    supporting_paths=["app/worker.py"],
                )
            ],
        )
    )

    narrative = await build_architecture_narrative(
        llm, _pattern_claim(), [], [_tradeoff_card("queue the work")], [], [_module_unit("app/worker.py")]
    )

    assert narrative is not None
    assert narrative.overview.startswith("Requests come in")
    assert [s.heading for s in narrative.why_sections] == ["Why indexing runs in a worker"]
    assert [(c.file_path, c.line_start, c.line_end) for c in narrative.citations] == [("app/worker.py", 1, 40)]


async def test_citation_excerpt_covers_heading_and_body() -> None:
    # One claim made by one call — a lookup by excerpt should return the whole
    # rendered block, not only its title.
    llm = _llm(
        ArchitectureNarrativeOutput(
            overview="o",
            why_sections=[WhySection(heading="Why queued", body="Because it's slow.", supporting_paths=["a.py"])],
        )
    )

    narrative = await build_architecture_narrative(llm, _pattern_claim(), [], [], [], [_module_unit("a.py")])

    assert narrative is not None
    assert narrative.citations[0].claim_excerpt == "Why queued — Because it's slow."


async def test_unknown_supporting_path_is_dropped_not_fabricated() -> None:
    llm = _llm(
        ArchitectureNarrativeOutput(
            overview="o",
            why_sections=[
                WhySection(heading="h", body="b", supporting_paths=["app/real.py", "app/imagined.py"])
            ],
        )
    )

    narrative = await build_architecture_narrative(
        llm, _pattern_claim(), [], [], [], [_module_unit("app/real.py")]
    )

    assert narrative is not None
    assert [c.file_path for c in narrative.citations] == ["app/real.py"]


async def test_duplicate_supporting_paths_cite_once() -> None:
    llm = _llm(
        ArchitectureNarrativeOutput(
            overview="o",
            why_sections=[WhySection(heading="h", body="b", supporting_paths=["a.py", "a.py"])],
        )
    )

    narrative = await build_architecture_narrative(llm, _pattern_claim(), [], [], [], [_module_unit("a.py")])

    assert narrative is not None
    assert len(narrative.citations) == 1


async def test_blank_sections_are_skipped() -> None:
    llm = _llm(
        ArchitectureNarrativeOutput(
            overview="o",
            why_sections=[
                WhySection(heading="   ", body="b", supporting_paths=[]),
                WhySection(heading="h", body="  ", supporting_paths=[]),
                WhySection(heading="real", body="kept", supporting_paths=[]),
            ],
        )
    )

    narrative = await build_architecture_narrative(llm, _pattern_claim(), [], [], [], [])

    assert narrative is not None
    assert [s.heading for s in narrative.why_sections] == ["real"]


async def test_empty_response_yields_no_narrative() -> None:
    # Nothing to render is not the same as a section made of whitespace.
    llm = _llm(ArchitectureNarrativeOutput(overview="   ", why_sections=[]))

    assert await build_architecture_narrative(llm, _pattern_claim(), [], [], [], []) is None


async def test_prompt_includes_subsystems_pattern_and_tradeoffs() -> None:
    llm = _llm(ArchitectureNarrativeOutput(overview="o"))

    await build_architecture_narrative(
        llm,
        _pattern_claim(),
        [_subsystem("app/worker", "Background worker", ["app/worker.py"])],
        [_tradeoff_card("queue the indexing work")],
        [{"file": "app/main.py", "kind": "http", "reason": "instantiates FastAPI()"}],
        [],
    )

    sent = llm.calls[0].messages[0].content
    assert "modular monolith" in sent
    assert "the worker is nearly its own service" in sent  # caveats reach the model
    assert "Background worker" in sent
    assert "queue the indexing work" in sent
    assert "instantiates FastAPI()" in sent


async def test_subsystems_reach_the_prompt_in_partition_order() -> None:
    llm = _llm(ArchitectureNarrativeOutput(overview="o"))

    await build_architecture_narrative(
        llm,
        _pattern_claim(),
        [_subsystem("b", "Second", ["b.py"], order=1), _subsystem("a", "First", ["a.py"], order=0)],
        [],
        [],
        [],
    )

    sent = llm.calls[0].messages[0].content
    assert sent.index('"First"') < sent.index('"Second"')


async def test_prompt_inputs_are_bounded() -> None:
    # Each persisted input is bounded by its own producer, but nothing bounds
    # them in combination — and this is the most expensive model tier.
    big_subsystem = _subsystem(
        "app", "Everything", [f"app/f{i:04d}.py" for i in range(MAX_FILES_PER_SUBSYSTEM_IN_PROMPT + 40)]
    )
    cards = [_tradeoff_card(f"decision {i:03d}") for i in range(MAX_TRADEOFF_CARDS_IN_PROMPT + 10)]
    llm = _llm(ArchitectureNarrativeOutput(overview="o"))

    await build_architecture_narrative(llm, _pattern_claim(), [big_subsystem], cards, [], [])

    sent = llm.calls[0].messages[0].content
    assert f"app/f{MAX_FILES_PER_SUBSYSTEM_IN_PROMPT - 1:04d}.py" in sent
    assert f"app/f{MAX_FILES_PER_SUBSYSTEM_IN_PROMPT:04d}.py" not in sent
    assert sent.count('"decision ') == MAX_TRADEOFF_CARDS_IN_PROMPT


async def test_runs_with_subsystems_but_no_pattern_claim() -> None:
    # detect_pattern returns None when every evidence item loses its citations;
    # the narrative still has subsystems and trade-offs to work from.
    llm = _llm(ArchitectureNarrativeOutput(overview="Still explainable."))

    narrative = await build_architecture_narrative(
        llm, None, [_subsystem("app", "App", ["app/a.py"])], [], [], []
    )

    assert narrative is not None
    assert narrative.overview == "Still explainable."
    assert "none detected" in llm.calls[0].messages[0].content
