import uuid

from app.db.models import CodeUnit, UnitType
from app.semantics.chunking import ModuleChunk
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.semantics.module_summarizer import ModuleSummaryOutput, summarize_modules


def _module_unit(file_path: str, line_end: int) -> CodeUnit:
    return CodeUnit(
        snapshot_id=uuid.uuid4(), file_path=file_path, unit_type=UnitType.module, name=file_path, line_start=1, line_end=line_end
    )


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
