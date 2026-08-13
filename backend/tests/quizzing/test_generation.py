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
    citations = [_citation(section_id, file_path=f"app/mod_{i}.py") for i in range(20)]

    seeds = identify_question_seeds(citations, section_type_by_id, limit=5)

    assert len(seeds) == 5


def test_identify_question_seeds_orders_by_priority_then_file_path() -> None:
    overview_section = uuid.uuid4()
    tradeoffs_section = uuid.uuid4()
    section_type_by_id = {overview_section: SectionType.overview, tradeoffs_section: SectionType.tradeoffs}

    seeds = identify_question_seeds(
        [
            _citation(overview_section, file_path="z_overview.py"),
            _citation(tradeoffs_section, file_path="a_tradeoffs.py"),
        ],
        section_type_by_id,
    )

    assert [s.file_path for s in seeds] == ["a_tradeoffs.py", "z_overview.py"]
