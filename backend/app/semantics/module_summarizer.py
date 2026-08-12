"""Implements docs/prompts/module_summarizer.v1.md (PROJECT_PLAN.md §9.1):
one LLM call per module chunk, grounded in Layer A facts already persisted as
CodeUnit rows — no source re-read needed. Citation for each summary is the
module unit's own line range (Ground Rule #3: every LLM claim needs a citation).
"""

import json
from dataclasses import dataclass

from pydantic import BaseModel

from app.db.models import CodeUnit
from app.semantics.chunking import ModuleChunk
from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt


class ModuleSummaryOutput(BaseModel):
    purpose: str
    role_in_system: str
    key_concepts: list[str]


@dataclass(frozen=True)
class ModuleSummaryResult:
    file_path: str
    purpose: str
    role_in_system: str
    key_concepts: list[str]
    line_start: int
    line_end: int
    prompt_version: str
    model: str


def _import_list(file_path: str, dependency_graph: dict) -> list[str]:
    return [edge["target"] for edge in dependency_graph.get("edges", []) if edge["source"] == file_path]


def _unit_dict(unit: CodeUnit) -> dict:
    return {
        "name": unit.name,
        "unit_type": unit.unit_type.value,
        "line_start": unit.line_start,
        "line_end": unit.line_end,
        "signature": unit.signature,
        "docstring": unit.docstring,
    }


async def summarize_modules(
    llm: LLMProvider, chunks: list[ModuleChunk], dependency_graph: dict
) -> list[ModuleSummaryResult]:
    template = load_prompt("module_summarizer")
    results: list[ModuleSummaryResult] = []

    for chunk in chunks:
        input_text = template.render_input(
            file_path=chunk.file_path,
            import_list=_import_list(chunk.file_path, dependency_graph),
            code_units_json=json.dumps([_unit_dict(chunk.module_unit), *(_unit_dict(u) for u in chunk.units)]),
        )
        response = await llm.complete(
            system=template.system,
            messages=[Message(role="user", content=input_text)],
            response_schema=ModuleSummaryOutput,
        )
        output = response.parsed
        assert isinstance(output, ModuleSummaryOutput)

        results.append(
            ModuleSummaryResult(
                file_path=chunk.file_path,
                purpose=output.purpose,
                role_in_system=output.role_in_system,
                key_concepts=output.key_concepts,
                line_start=chunk.module_unit.line_start,
                line_end=chunk.module_unit.line_end,
                prompt_version=template.version,
                model=response.model,
            )
        )

    return results
