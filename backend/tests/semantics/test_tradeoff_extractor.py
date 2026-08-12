import uuid
from pathlib import Path

from app.db.models import CodeUnit, UnitType
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.tradeoff_extractor import (
    EvidenceRef,
    TradeoffCardOutput,
    _read_snippet,
    extract_tradeoffs,
    identify_decision_points,
)


def _module_unit(file_path: str, line_end: int) -> CodeUnit:
    return CodeUnit(
        snapshot_id=uuid.uuid4(), file_path=file_path, unit_type=UnitType.module, name=file_path, line_start=1, line_end=line_end
    )


def test_identify_decision_points_flags_infra_import_and_high_fan_in() -> None:
    code_units = [
        _module_unit("app/worker.py", 20),
        _module_unit("app/hub.py", 15),
        _module_unit("app/leaf_a.py", 5),
        _module_unit("app/leaf_b.py", 5),
    ]
    dependency_graph = {
        "nodes": [
            {"id": "app/worker.py", "kind": "file", "language": "python"},
            {"id": "app/hub.py", "kind": "file", "language": "python"},
            {"id": "app/leaf_a.py", "kind": "file", "language": "python"},
            {"id": "app/leaf_b.py", "kind": "file", "language": "python"},
        ],
        "edges": [
            {"source": "app/worker.py", "target": "external:arq", "kind": "imports_external"},
            {"source": "app/leaf_a.py", "target": "app/hub.py", "kind": "imports"},
            {"source": "app/leaf_b.py", "target": "app/hub.py", "kind": "imports"},
        ],
    }

    candidates = identify_decision_points(dependency_graph, code_units, entry_points=[], limit=8)

    labels = [c.label for c in candidates]
    assert "app/worker.py" in labels  # infra import — boosted above fan-in/out
    assert "app/hub.py" in labels  # fan-in=2
    # ranked by score: infra import first, then fan-in=2, then the fan-in=1 leaves
    assert labels.index("app/worker.py") < labels.index("app/hub.py") < labels.index("app/leaf_a.py")


def test_identify_decision_points_respects_limit() -> None:
    code_units = [_module_unit(f"app/mod_{i}.py", 5) for i in range(10)]
    nodes = [{"id": f"app/mod_{i}.py", "kind": "file", "language": "python"} for i in range(10)]
    edges = [{"source": f"app/mod_{i}.py", "target": "external:requests", "kind": "imports_external"} for i in range(10)]

    candidates = identify_decision_points({"nodes": nodes, "edges": edges}, code_units, entry_points=[], limit=3)

    assert len(candidates) == 3


async def test_extract_tradeoffs_seeds_citation_and_validates_refs(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("import arq\n\n\ndef main():\n    pass\n")

    code_units = [_module_unit("app.py", 5)]
    candidates = identify_decision_points(
        {
            "nodes": [{"id": "app.py", "kind": "file", "language": "python"}],
            "edges": [{"source": "app.py", "target": "external:arq", "kind": "imports_external"}],
        },
        code_units,
        entry_points=[],
    )
    assert len(candidates) == 1

    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=TradeoffCardOutput(
                    decision="use arq",
                    alternatives_considered=["celery"],
                    likely_reasoning="lighter weight",
                    tradeoff_cost="another moving part",
                    confidence="medium",
                    evidence_refs=[
                        EvidenceRef(file_path="app.py", line_start=1, line_end=5),  # valid
                        EvidenceRef(file_path="app.py", line_start=1, line_end=999),  # out of range
                        EvidenceRef(file_path="does_not_exist.py", line_start=1, line_end=1),  # unknown file
                    ],
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    results = await extract_tradeoffs(llm, candidates, source_dir, {"edges": []}, code_units)

    assert len(results) == 1
    result = results[0]
    assert result.decision == "use arq"
    # seed citation (from the decision point itself) always present first, plus the one valid ref
    assert result.evidence_refs[0] == {"file_path": "app.py", "line_start": 1, "line_end": 5}
    assert len(result.evidence_refs) == 2
    assert result.evidence_refs[1]["line_end"] == 5

    # code_snippet passed to the LLM was actually read from disk
    assert "import arq" in llm.calls[0].messages[0].content


async def test_extract_tradeoffs_drops_card_with_no_validated_refs(tmp_path: Path) -> None:
    # Found via Codex's Phase 2 pre-push review: the seed citation (from the
    # deterministic decision-point heuristic) only explains why this file was
    # flagged — it doesn't substantiate the LLM's actual decision/reasoning
    # claims. A card with zero LLM-validated refs must be dropped, not
    # persisted with only the seed making it look grounded.
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("import arq\n\n\ndef main():\n    pass\n")

    code_units = [_module_unit("app.py", 5)]
    candidates = identify_decision_points(
        {
            "nodes": [{"id": "app.py", "kind": "file", "language": "python"}],
            "edges": [{"source": "app.py", "target": "external:arq", "kind": "imports_external"}],
        },
        code_units,
        entry_points=[],
    )

    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=TradeoffCardOutput(
                    decision="use arq",
                    alternatives_considered=["celery"],
                    likely_reasoning="lighter weight",
                    tradeoff_cost="another moving part",
                    confidence="medium",
                    evidence_refs=[
                        EvidenceRef(file_path="app.py", line_start=1, line_end=999),  # out of range
                        EvidenceRef(file_path="does_not_exist.py", line_start=1, line_end=1),  # unknown file
                    ],
                ),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    results = await extract_tradeoffs(llm, candidates, source_dir, {"edges": []}, code_units)

    assert results == []


def test_read_snippet_tolerates_non_utf8_bytes(tmp_path: Path) -> None:
    # Found via Codex's Phase 2 pre-push review: parser.py parses on raw bytes
    # and never raises on non-UTF-8 source, so a Latin-1-encoded file that
    # Layer A parsed successfully must not fail Layer B's strict-UTF-8 read.
    (tmp_path / "latin1.py").write_bytes("# café\nx = 1\n".encode("latin-1"))

    snippet = _read_snippet(tmp_path, "latin1.py", 1, 2)

    assert "x = 1" in snippet  # doesn't raise UnicodeDecodeError
    assert "�" in snippet  # the non-UTF-8 byte decodes to a replacement char
