import uuid

from app.db.models import CodeUnit, UnitType
from app.semantics.chunking import ModuleChunk
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.module_summarizer import MAX_FIELD_CHARS, ModuleSummaryOutput, _truncate, summarize_modules


def _module_unit(file_path: str, line_end: int) -> CodeUnit:
    return CodeUnit(
        snapshot_id=uuid.uuid4(), file_path=file_path, unit_type=UnitType.module, name=file_path, line_start=1, line_end=line_end
    )


def test_truncate_caps_oversized_fields() -> None:
    # Found via Codex's PR #17 review (#20): docstring/signature had no size
    # bound of their own, unlike pattern_detector's graph and
    # tradeoff_extractor's file reads. An unusually large docstring (e.g. an
    # embedded changelog) could still skew a chunk's budget.
    oversized = "x" * (MAX_FIELD_CHARS + 100)

    truncated = _truncate(oversized)

    assert len(truncated) <= MAX_FIELD_CHARS + 100
    assert "truncated" in truncated
    assert _truncate(None) is None
    assert _truncate("short") == "short"


async def test_summarize_modules_truncates_oversized_docstring_reaching_the_llm() -> None:
    module_unit = _module_unit("app/main.py", 42)
    module_unit.docstring = "x" * (MAX_FIELD_CHARS + 100)
    chunk = ModuleChunk(file_path="app/main.py", module_unit=module_unit, units=[])
    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=ModuleSummaryOutput(purpose="p", role_in_system="r", key_concepts=[]),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    await summarize_modules(llm, [chunk], {"edges": []})

    sent_input = llm.calls[0].messages[0].content
    assert "x" * (MAX_FIELD_CHARS + 100) not in sent_input
    assert "truncated" in sent_input


async def test_summarize_modules_builds_result_from_parsed_output() -> None:
    module_unit = _module_unit("app/main.py", 42)
    chunk = ModuleChunk(file_path="app/main.py", module_unit=module_unit, units=[])
    dependency_graph = {
        "edges": [
            {"source": "app/main.py", "target": "app/db.py", "kind": "imports"},
            {"source": "app/main.py", "target": "external:fastapi", "kind": "imports_external"},
            {"source": "app/other.py", "target": "app/main.py", "kind": "imports"},  # not this file's import
        ]
    }
    llm = FakeLLMProvider(
        [
            LLMResponse(
                text="",
                parsed=ModuleSummaryOutput(purpose="entrypoint", role_in_system="wires the app", key_concepts=["FastAPI"]),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            )
        ]
    )

    results = await summarize_modules(llm, [chunk], dependency_graph)

    assert len(results) == 1
    result = results[0]
    assert result.file_path == "app/main.py"
    assert result.purpose == "entrypoint"
    assert result.role_in_system == "wires the app"
    assert result.key_concepts == ["FastAPI"]
    assert result.line_start == 1
    assert result.line_end == 42
    assert result.prompt_version == "v1"
    assert result.model == "fake-model"

    # {import_list} only includes this file's own outgoing edges
    call = llm.calls[0]
    assert "app/db.py" in call.messages[0].content
    assert "external:fastapi" in call.messages[0].content
    assert "app/other.py" not in call.messages[0].content


async def test_summarize_modules_skips_a_truncated_summary_and_keeps_the_rest() -> None:
    # One unusable response costs that module's summary only. Downstream
    # consumers already tolerate a missing entry (subsystem_namer reads
    # module_purposes with .get(); diagram_builder falls back to a path label).
    chunks = [
        ModuleChunk(file_path="app/main.py", module_unit=_module_unit("app/main.py", 42), units=[]),
        ModuleChunk(file_path="app/db.py", module_unit=_module_unit("app/db.py", 10), units=[]),
    ]
    llm = FakeLLMProvider(
        [
            LLMResponse(text="", parsed=None, model="fake-model", stop_reason="max_tokens", usage={"output_tokens": 8192}),
            LLMResponse(
                text="",
                parsed=ModuleSummaryOutput(purpose="db access", role_in_system="persistence", key_concepts=[]),
                model="fake-model",
                stop_reason="end_turn",
                usage={},
            ),
        ]
    )

    results = await summarize_modules(llm, chunks, {"edges": []})

    assert [r.file_path for r in results] == ["app/db.py"]
    assert len(llm.calls) == 2
