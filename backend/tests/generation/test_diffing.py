import uuid

from app.db.models import Confidence, PatternClaim, Subsystem, TradeoffCard
from app.generation.diffing import (
    diff_dependencies,
    diff_pattern,
    diff_subsystems,
    diff_tradeoffs,
)

_SNAPSHOT = uuid.uuid4()


def _subsystem(key: str, name: str, file_paths: list[str], order: int = 0) -> Subsystem:
    return Subsystem(
        snapshot_id=_SNAPSHOT, key=key, name=name, role="r", file_paths=file_paths,
        depth=0, order=order, prompt_version="v1", model="fake",
    )


def _card(decision: str, reasoning: str, cost: str, paths: list[str]) -> TradeoffCard:
    return TradeoffCard(
        snapshot_id=_SNAPSHOT, decision=decision, alternatives_considered=[], likely_reasoning=reasoning,
        tradeoff_cost=cost, confidence=Confidence.medium,
        evidence_refs=[{"file_path": p, "line_start": 1, "line_end": 10} for p in paths],
        prompt_version="v1", model="fake",
    )


def _pattern(name: str, confidence: Confidence = Confidence.medium) -> PatternClaim:
    return PatternClaim(
        snapshot_id=_SNAPSHOT, primary_pattern=name, confidence=confidence, evidence=[],
        caveats=None, prompt_version="v1", model="fake",
    )


def _graph(edges: list[tuple[str, str]]) -> dict:
    return {"nodes": [], "edges": [{"source": s, "target": t, "kind": "imports"} for s, t in edges]}


# --- subsystems ---


def test_added_and_removed_subsystems() -> None:
    before = [_subsystem("app/api", "HTTP API", ["app/api/a.py"])]
    after = [_subsystem("app/worker", "Background worker", ["app/worker/b.py"])]

    result = diff_subsystems(before, after)

    assert [s.key for s in result.added] == ["app/worker"]
    assert [s.key for s in result.removed] == ["app/api"]
    assert result.changed == []


def test_membership_change_is_reported_per_file() -> None:
    before = [_subsystem("app/api", "HTTP API", ["a.py", "b.py"])]
    after = [_subsystem("app/api", "HTTP API", ["b.py", "c.py"])]

    changed = diff_subsystems(before, after).changed

    assert len(changed) == 1
    assert changed[0].files_added == ["c.py"]
    assert changed[0].files_removed == ["a.py"]


def test_a_renamed_subsystem_is_not_a_change() -> None:
    # Names are LLM output and drift between runs; the key is Layer A. Treating
    # a reworded name as architectural change is the single biggest way this
    # feature would become noise nobody reads.
    before = [_subsystem("app/semantics", "Semantic analysis", ["a.py"])]
    after = [_subsystem("app/semantics", "Meaning extraction", ["a.py"])]

    result = diff_subsystems(before, after)

    assert result.added == [] and result.removed == [] and result.changed == []


def test_identical_snapshots_produce_an_empty_subsystem_diff() -> None:
    subsystems = [_subsystem("app/api", "HTTP API", ["a.py", "b.py"])]

    result = diff_subsystems(subsystems, list(subsystems))

    assert result.added == [] and result.removed == [] and result.changed == []


# --- trade-offs ---


def test_reworded_tradeoff_over_the_same_evidence_is_a_change_not_a_replacement() -> None:
    before = [_card("use a queue", "indexing is slow", "more infra", ["app/worker.py"])]
    after = [_card("queue the indexing work", "indexing takes minutes", "another moving part", ["app/worker.py"])]

    result = diff_tradeoffs(before, after)

    assert result.added == [] and result.removed == []
    assert len(result.changed) == 1
    assert result.changed[0].reasoning_before == "indexing is slow"
    assert result.changed[0].reasoning_after == "indexing takes minutes"


def test_identical_tradeoffs_produce_no_diff() -> None:
    cards = [_card("use a queue", "slow", "infra", ["app/worker.py"])]

    result = diff_tradeoffs(cards, [_card("use a queue", "slow", "infra", ["app/worker.py"])])

    assert result.added == [] and result.removed == [] and result.changed == []


def test_a_tradeoff_whose_evidence_moved_is_removed_and_added() -> None:
    # Honest rather than clever: its grounding genuinely changed, and pairing
    # them anyway would mean guessing from prose.
    before = [_card("use a queue", "slow", "infra", ["app/worker.py"])]
    after = [_card("use a queue", "slow", "infra", ["app/jobs.py"])]

    result = diff_tradeoffs(before, after)

    assert result.added == ["use a queue"]
    assert result.removed == ["use a queue"]
    assert result.changed == []


def test_new_and_dropped_tradeoffs() -> None:
    before = [_card("use a queue", "slow", "infra", ["a.py"])]
    after = [
        _card("use a queue", "slow", "infra", ["a.py"]),
        _card("cache settings", "hot path", "staleness", ["b.py"]),
    ]

    result = diff_tradeoffs(before, after)

    assert result.added == ["cache settings"]
    assert result.removed == []


# --- pattern ---


def test_pattern_change_is_detected() -> None:
    result = diff_pattern(_pattern("modular monolith"), _pattern("layered"))
    assert result.changed
    assert (result.pattern_before, result.pattern_after) == ("modular monolith", "layered")


def test_confidence_alone_counts_as_a_pattern_change() -> None:
    result = diff_pattern(_pattern("layered", Confidence.low), _pattern("layered", Confidence.high))
    assert result.changed
    assert (result.confidence_before, result.confidence_after) == ("low", "high")


def test_unchanged_pattern_reports_no_change() -> None:
    assert not diff_pattern(_pattern("layered"), _pattern("layered")).changed


def test_missing_pattern_claims_are_tolerated() -> None:
    # detect_pattern returns None when every evidence item loses its citations.
    assert diff_pattern(None, None).changed is False
    assert diff_pattern(None, _pattern("layered")).changed is True


# --- dependencies ---


def test_dependency_edges_are_reported_between_subsystems_not_files() -> None:
    # A refactor moving twenty files should read as one line, not forty.
    subsystems = [_subsystem("app/api", "API", ["app/api/a.py"]), _subsystem("app/db", "DB", ["app/db/b.py"])]
    before = _graph([])
    after = _graph([("app/api/a.py", "app/db/b.py")])

    result = diff_dependencies(before, subsystems, after, subsystems)

    assert [(e.source, e.target) for e in result.edges_added] == [("app/api", "app/db")]
    assert result.edges_removed == []


def test_edges_within_one_subsystem_are_not_architectural() -> None:
    subsystems = [_subsystem("app/api", "API", ["app/api/a.py", "app/api/b.py"])]
    after = _graph([("app/api/a.py", "app/api/b.py")])

    result = diff_dependencies(_graph([]), subsystems, after, subsystems)

    assert result.edges_added == []


def test_many_file_edges_collapse_to_one_subsystem_edge() -> None:
    subsystems = [
        _subsystem("app/api", "API", [f"app/api/f{i}.py" for i in range(10)]),
        _subsystem("app/db", "DB", [f"app/db/f{i}.py" for i in range(10)]),
    ]
    after = _graph([(f"app/api/f{i}.py", f"app/db/f{i}.py") for i in range(10)])

    result = diff_dependencies(_graph([]), subsystems, after, subsystems)

    assert len(result.edges_added) == 1


def test_external_dependencies_are_surfaced() -> None:
    # Gaining a dependency on Redis is exactly the kind of change worth seeing.
    subsystems = [_subsystem("app/worker", "Worker", ["app/worker/a.py"])]
    after = {"nodes": [], "edges": [{"source": "app/worker/a.py", "target": "external:redis", "kind": "imports_external"}]}

    result = diff_dependencies(_graph([]), subsystems, after, subsystems)

    assert [(e.source, e.target) for e in result.edges_added] == [("app/worker", "external:redis")]


def test_files_in_no_subsystem_are_skipped() -> None:
    subsystems = [_subsystem("app/api", "API", ["app/api/a.py"])]
    after = _graph([("stray.py", "app/api/a.py")])

    result = diff_dependencies(_graph([]), subsystems, after, subsystems)

    assert result.edges_added == []


def test_removed_edges_are_reported() -> None:
    subsystems = [_subsystem("app/api", "API", ["app/api/a.py"]), _subsystem("app/db", "DB", ["app/db/b.py"])]
    before = _graph([("app/api/a.py", "app/db/b.py")])

    result = diff_dependencies(before, subsystems, _graph([]), subsystems)

    assert [(e.source, e.target) for e in result.edges_removed] == [("app/api", "app/db")]
