import uuid

from app.db.models import CodeUnit, UnitType
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.pattern_detector import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    PatternClaimOutput,
    PatternEvidenceItem,
    _bound_graph,
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


def test_bound_graph_caps_nodes_deterministically() -> None:
    # Found via Codex's Phase 2 pre-push review: git-cloned repos have no
    # file-count cap (unlike zip uploads, capped at 5,000), so a large repo's
    # dependency graph could blow the LLM request past its context limit
    # unbounded. Truncation must be deterministic — the same node subset
    # every run, not whatever order build_dependency_graph happened to emit.
    node_count = MAX_GRAPH_NODES + 10
    nodes = [{"id": f"file_{i:04d}.py", "kind": "file", "language": "python"} for i in range(node_count)]
    dependency_graph = {"nodes": nodes, "edges": []}

    bounded = _bound_graph(dependency_graph)

    assert len(bounded["nodes"]) == MAX_GRAPH_NODES
    assert {n["id"] for n in bounded["nodes"]} == {f"file_{i:04d}.py" for i in range(MAX_GRAPH_NODES)}


def test_bound_graph_prioritizes_file_nodes_over_external_nodes() -> None:
    # Found via Codex's Phase 2 pre-push review round 8: sorting every node
    # together let "external:*" package nodes (often alphabetically early)
    # crowd real files out of the fixed node budget on a dependency-heavy
    # repo. All files must survive if they fit within the budget, even when
    # there are far more external nodes than remaining room.
    file_nodes = [{"id": f"src/file_{i:04d}.py", "kind": "file", "language": "python"} for i in range(10)]
    external_nodes = [
        {"id": f"external:package_{i:04d}", "kind": "external", "language": None}
        for i in range(MAX_GRAPH_NODES + 10)
    ]
    edges = [
        {"source": "src/file_0000.py", "target": n["id"], "kind": "imports_external"} for n in external_nodes
    ]
    dependency_graph = {"nodes": file_nodes + external_nodes, "edges": edges}

    bounded = _bound_graph(dependency_graph)

    kept_ids = {n["id"] for n in bounded["nodes"]}
    assert {n["id"] for n in file_nodes} <= kept_ids
    assert len(bounded["nodes"]) == MAX_GRAPH_NODES


def test_bound_graph_drops_external_nodes_not_adjacent_to_a_kept_file() -> None:
    # An external node with no edge to any retained file is dropped even
    # when budget remains — it isn't evidence of an actual dependency
    # relationship the model could ground a claim in.
    file_nodes = [{"id": "src/a.py", "kind": "file", "language": "python"}]
    connected = {"id": "external:used", "kind": "external", "language": None}
    orphan = {"id": "external:unused", "kind": "external", "language": None}
    dependency_graph = {
        "nodes": [*file_nodes, connected, orphan],
        "edges": [{"source": "src/a.py", "target": "external:used", "kind": "imports_external"}],
    }

    bounded = _bound_graph(dependency_graph)

    kept_ids = {n["id"] for n in bounded["nodes"]}
    assert kept_ids == {"src/a.py", "external:used"}


def test_bound_graph_drops_edges_touching_a_truncated_node() -> None:
    # An edge referencing a node that got cut by the node cap must not
    # survive — otherwise the graph handed to the model references a node it
    # never actually sees, which is worse than dropping the edge.
    nodes = [{"id": f"file_{i:04d}.py", "kind": "file", "language": "python"} for i in range(MAX_GRAPH_NODES + 1)]
    kept_edge = {"source": "file_0000.py", "target": "file_0001.py", "kind": "imports"}
    dropped_edge = {"source": "file_0000.py", "target": f"file_{MAX_GRAPH_NODES:04d}.py", "kind": "imports"}
    dependency_graph = {"nodes": nodes, "edges": [kept_edge, dropped_edge]}

    bounded = _bound_graph(dependency_graph)

    assert bounded["edges"] == [kept_edge]


def test_bound_graph_caps_edges() -> None:
    nodes = [{"id": f"file_{i:04d}.py", "kind": "file", "language": "python"} for i in range(2)]
    edges = [
        {"source": "file_0000.py", "target": "file_0001.py", "kind": f"kind_{i:04d}"}
        for i in range(MAX_GRAPH_EDGES + 10)
    ]
    dependency_graph = {"nodes": nodes, "edges": edges}

    bounded = _bound_graph(dependency_graph)

    assert len(bounded["edges"]) == MAX_GRAPH_EDGES


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
    # persisted supporting_paths must match what actually resolved, not the
    # LLM's raw list — app/missing.py must not appear here either
    assert set(result.evidence[0]["supporting_paths"]) == {"app/api.py", "app/db.py"}


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


async def test_detect_pattern_drops_evidence_item_with_no_resolvable_citations() -> None:
    # Found via Codex's Phase 2 pre-push review: an evidence item whose every
    # supporting_path is unresolvable must not be persisted with an empty
    # citations list — that's an uncited claim, violating Ground Rule #3.
    code_units = [_module_unit("app/real.py", 10)]
    dependency_graph = {
        "nodes": [{"id": "app/real.py", "kind": "file", "language": "python"}],
        "edges": [],
    }
    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(
                    primary_pattern="layered",
                    confidence="low",
                    evidence=[
                        PatternEvidenceItem(claim="grounded claim", supporting_paths=["app/real.py"]),
                        PatternEvidenceItem(
                            claim="ungrounded claim", supporting_paths=["app/imaginary.py", "app/also_missing.py"]
                        ),
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

    assert result is not None
    assert [e["claim"] for e in result.evidence] == ["grounded claim"]


async def test_detect_pattern_returns_none_when_no_evidence_resolves() -> None:
    # Found via Codex's Phase 2 pre-push review: if *every* evidence item
    # loses its citations, persisting primary_pattern anyway leaves the whole
    # architectural claim uncited, not just one item of it. No claim is
    # better than an ungrounded one.
    code_units = [_module_unit("app/real.py", 10)]
    dependency_graph = {"nodes": [{"id": "app/real.py", "kind": "file", "language": "python"}], "edges": []}
    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=PatternClaimOutput(
                    primary_pattern="layered",
                    confidence="low",
                    evidence=[PatternEvidenceItem(claim="ungrounded claim", supporting_paths=["app/imaginary.py"])],
                    caveats=None,
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    result = await detect_pattern(llm, dependency_graph, code_units, entry_points=[])

    assert result is None
