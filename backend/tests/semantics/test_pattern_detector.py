import uuid

from app.db.models import CodeUnit, UnitType
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.pattern_detector import (
    PatternClaimOutput,
    PatternEvidenceItem,
    _render_directory_tree,
    detect_pattern,
)


def _module_unit(file_path: str, line_end: int) -> CodeUnit:
    return CodeUnit(
        snapshot_id=uuid.uuid4(), file_path=file_path, unit_type=UnitType.module, name=file_path, line_start=1, line_end=line_end
    )


def test_render_directory_tree_is_deterministic() -> None:
    tree = _render_directory_tree(["b/two.py", "a.py", "b/one.py"])
    assert tree == "a.py\nb\n  one.py\n  two.py"


async def test_detect_pattern_resolves_and_drops_citations() -> None:
    code_units = [_module_unit("app/api.py", 30), _module_unit("app/db.py", 10)]
    dependency_graph = {
        "nodes": [
            {"id": "app/api.py", "kind": "file", "language": "python"},
            {"id": "app/db.py", "kind": "file", "language": "python"},
        ],
        "edges": [{"source": "app/api.py", "target": "app/db.py", "kind": "imports"}],
    }
    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(
                    primary_pattern="layered",
                    confidence="high",
                    evidence=[
                        PatternEvidenceItem(
                            claim="api depends on db", supporting_paths=["app/api.py", "app/db.py", "app/missing.py"]
                        )
                    ],
                    caveats=None,
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    result = await detect_pattern(llm, dependency_graph, code_units, entry_points=[])

    assert result.primary_pattern == "layered"
    assert result.confidence == "high"
    assert len(result.evidence) == 1
    citations = result.evidence[0]["citations"]
    # app/missing.py has no matching CodeUnit — dropped, not fabricated
    assert {c["file_path"] for c in citations} == {"app/api.py", "app/db.py"}
    assert {"file_path": "app/api.py", "line_start": 1, "line_end": 30} in citations


async def test_detect_pattern_resolves_relationship_style_evidence_paths() -> None:
    # Found via the Phase 2 manual checkpoint against strata-learn's own
    # backend: the LLM sometimes writes evidence about a relationship as
    # "A -> B" instead of a bare path, silently losing all citations for
    # that evidence item. Both real endpoints of the relationship must still
    # resolve; an unknown endpoint on one side of the arrow is dropped like
    # any other unresolved path, without discarding the resolvable side.
    code_units = [_module_unit("app/worker/pipeline.py", 50), _module_unit("app/analysis/snapshot.py", 40)]
    dependency_graph = {
        "nodes": [
            {"id": "app/worker/pipeline.py", "kind": "file", "language": "python"},
            {"id": "app/analysis/snapshot.py", "kind": "file", "language": "python"},
        ],
        "edges": [{"source": "app/worker/pipeline.py", "target": "app/analysis/snapshot.py", "kind": "imports"}],
    }
    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(
                    primary_pattern="pipeline/orchestrator",
                    confidence="medium",
                    evidence=[
                        PatternEvidenceItem(
                            claim="pipeline chains Layer A into the worker",
                            supporting_paths=[
                                "app/worker/pipeline.py -> app/analysis/snapshot.py",
                                "app/worker/pipeline.py -> app/does_not_exist.py",
                            ],
                        )
                    ],
                    caveats=None,
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    result = await detect_pattern(llm, dependency_graph, code_units, entry_points=[])

    citations = result.evidence[0]["citations"]
    assert {c["file_path"] for c in citations} == {"app/worker/pipeline.py", "app/analysis/snapshot.py"}
    # app/worker/pipeline.py appears on both arrow-notation entries but is only cited once
    assert len(citations) == 2
