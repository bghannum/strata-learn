import uuid

from app.db.models import Citation, SectionType
from app.quizzing.generation import identify_question_seeds


def _citation(section_id, file_path="app.py", line_start=1, line_end=5, claim="claim") -> Citation:
    return Citation(
        section_id=section_id, file_path=file_path, line_start=line_start, line_end=line_end,
        claim_excerpt=claim, snippet_text="code",
    )


def test_identify_question_seeds_prefers_deep_dive_over_overview_for_same_range() -> None:
    overview_section = uuid.uuid4()
    deep_dive_section = uuid.uuid4()
    section_type_by_id = {overview_section: SectionType.overview, deep_dive_section: SectionType.deep_dive}

    overview_citation = _citation(overview_section, claim="overview claim")
    deep_dive_citation = _citation(deep_dive_section, claim="deep dive claim")

    seeds = identify_question_seeds([overview_citation, deep_dive_citation], section_type_by_id)

    assert len(seeds) == 1  # same (file_path, line_start, line_end) — deduped
    assert seeds[0].claim_excerpt == "deep dive claim"


def test_identify_question_seeds_respects_limit() -> None:
    section_id = uuid.uuid4()
    section_type_by_id = {section_id: SectionType.deep_dive}
    citations = [
        _citation(section_id, file_path=f"app/mod_{i}.py", claim=f"claim {i}") for i in range(20)
    ]

    seeds = identify_question_seeds(citations, section_type_by_id, limit=5)

    assert len(seeds) == 5


def test_identify_question_seeds_orders_by_priority_then_file_path() -> None:
    overview_section = uuid.uuid4()
    tradeoffs_section = uuid.uuid4()
    section_type_by_id = {overview_section: SectionType.overview, tradeoffs_section: SectionType.tradeoffs}

    seeds = identify_question_seeds(
        [
            _citation(overview_section, file_path="z_overview.py", claim="overview claim"),
            _citation(tradeoffs_section, file_path="a_tradeoffs.py", claim="tradeoff claim"),
        ],
        section_type_by_id,
    )

    assert [s.file_path for s in seeds] == ["a_tradeoffs.py", "z_overview.py"]


# --- #51: overlapping coverage ---


def test_one_tradeoff_card_seeds_only_one_question() -> None:
    # study_guide_builder gives every evidence_ref of a card the same
    # claim_excerpt (the whole card text) at different line ranges. Range dedup
    # can't see that, so all of them used to survive and generate several
    # questions from one piece of material — the reported symptom.
    section_id = uuid.uuid4()
    section_type_by_id = {section_id: SectionType.tradeoffs}
    card_claim = "use a queue — indexing takes minutes. Trade-off: more infra."
    citations = [
        _citation(section_id, file_path="app/worker.py", line_start=1, line_end=10, claim=card_claim),
        _citation(section_id, file_path="app/api.py", line_start=20, line_end=30, claim=card_claim),
        _citation(section_id, file_path="app/queue.py", line_start=5, line_end=15, claim=card_claim),
    ]

    seeds = identify_question_seeds(citations, section_type_by_id)

    assert len(seeds) == 1


def test_claim_dedup_ignores_whitespace_and_case() -> None:
    section_id = uuid.uuid4()
    section_type_by_id = {section_id: SectionType.tradeoffs}
    citations = [
        _citation(section_id, file_path="a.py", claim="Use  a queue\nfor indexing"),
        _citation(section_id, file_path="b.py", line_start=9, claim="use a queue for indexing"),
    ]

    seeds = identify_question_seeds(citations, section_type_by_id)

    assert len(seeds) == 1


def test_claim_dedup_keeps_the_highest_priority_copy() -> None:
    glossary_section = uuid.uuid4()
    tradeoffs_section = uuid.uuid4()
    section_type_by_id = {
        glossary_section: SectionType.glossary,
        tradeoffs_section: SectionType.tradeoffs,
    }
    citations = [
        _citation(glossary_section, file_path="z.py", line_start=50, claim="shared claim"),
        _citation(tradeoffs_section, file_path="a.py", line_start=1, claim="shared claim"),
    ]

    seeds = identify_question_seeds(citations, section_type_by_id)

    assert [s.file_path for s in seeds] == ["a.py"]


def test_seeds_spread_across_subsystems_instead_of_taking_one_directory() -> None:
    # Ranking within a tier was alphabetical by path, so a repo with plenty of
    # deep-dive citations could fill an entire quiz from whichever directory
    # sorts first and never reach the worker or API layers at all.
    section_id = uuid.uuid4()
    section_type_by_id = {section_id: SectionType.deep_dive}
    citations = [
        _citation(section_id, file_path=f"app/analysis/f{i}.py", claim=f"analysis {i}") for i in range(8)
    ] + [_citation(section_id, file_path=f"app/worker/f{i}.py", claim=f"worker {i}") for i in range(8)]
    subsystem_key_by_file = {c.file_path: c.file_path.rsplit("/", 1)[0] for c in citations}

    seeds = identify_question_seeds(
        citations, section_type_by_id, subsystem_key_by_file=subsystem_key_by_file, limit=4
    )

    assert {s.file_path.rsplit("/", 1)[0] for s in seeds} == {"app/analysis", "app/worker"}


def test_spreading_still_exhausts_the_better_tier_first() -> None:
    # Spreading happens *within* a priority tier — an overview blurb must not
    # displace trade-off material just because it's in another subsystem.
    tradeoffs_section = uuid.uuid4()
    overview_section = uuid.uuid4()
    section_type_by_id = {
        tradeoffs_section: SectionType.tradeoffs,
        overview_section: SectionType.overview,
    }
    citations = [
        _citation(tradeoffs_section, file_path=f"app/a/f{i}.py", claim=f"tradeoff {i}") for i in range(3)
    ] + [_citation(overview_section, file_path=f"app/b/f{i}.py", claim=f"overview {i}") for i in range(3)]
    subsystem_key_by_file = {c.file_path: c.file_path.rsplit("/", 1)[0] for c in citations}

    seeds = identify_question_seeds(
        citations, section_type_by_id, subsystem_key_by_file=subsystem_key_by_file, limit=3
    )

    assert all(s.file_path.startswith("app/a/") for s in seeds)


def test_files_without_a_subsystem_still_seed_questions() -> None:
    # A snapshot indexed before subsystems existed has no mapping at all.
    section_id = uuid.uuid4()
    section_type_by_id = {section_id: SectionType.deep_dive}
    citations = [_citation(section_id, file_path=f"f{i}.py", claim=f"claim {i}") for i in range(4)]

    seeds = identify_question_seeds(citations, section_type_by_id, subsystem_key_by_file={})

    assert len(seeds) == 4


def test_seed_selection_is_deterministic() -> None:
    section_id = uuid.uuid4()
    section_type_by_id = {section_id: SectionType.deep_dive}
    citations = [
        _citation(section_id, file_path=f"app/{d}/f{i}.py", claim=f"{d} {i}")
        for d in ("api", "worker", "db")
        for i in range(4)
    ]
    subsystem_key_by_file = {c.file_path: c.file_path.rsplit("/", 1)[0] for c in citations}

    first = identify_question_seeds(
        citations, section_type_by_id, subsystem_key_by_file=subsystem_key_by_file, limit=6
    )
    second = identify_question_seeds(
        list(reversed(citations)), section_type_by_id, subsystem_key_by_file=subsystem_key_by_file, limit=6
    )

    assert [s.file_path for s in first] == [s.file_path for s in second]
