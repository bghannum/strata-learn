"""Implements docs/prompts/pattern_detector.v1.md (PROJECT_PLAN.md §9.2): one
LLM call over the full dependency graph, producing a single architecture
pattern claim grounded in specific evidence. The LLM returns evidence as file
paths only (per the fixed prompt's OUTPUT schema) — it never invents line
numbers; this module resolves each path to a citation via the matching module
CodeUnit's own line range (Ground Rule #3), the same "code assigns citations,
not the LLM" split used in module_summarizer.py.
"""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.db.models import CodeUnit, UnitType
from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt


class PatternEvidenceItem(BaseModel):
    claim: str
    supporting_paths: list[str]


class PatternClaimOutput(BaseModel):
    primary_pattern: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[PatternEvidenceItem]
    caveats: str | None = None


@dataclass(frozen=True)
class PatternClaimResult:
    primary_pattern: str
    confidence: str
    evidence: list[dict]
    caveats: str | None
    prompt_version: str
    model: str


def _candidate_paths(raw: str) -> list[str]:
    """Found via the Phase 2 manual checkpoint: the prompt asks for bare file
    paths, but for evidence about a *relationship* (e.g. "this pipeline chains
    module A into module B") the LLM sometimes writes 'A -> B' instead of
    picking one side. Splitting on the arrow lets both real files still
    resolve to citations instead of the whole item silently losing all of
    them — a plain bare path is unaffected (split on an absent separator just
    returns the original string)."""
    return [part.strip() for part in raw.split(" -> ")]


def _render_directory_tree(file_paths: list[str]) -> str:
    tree: dict = {}
    for path in file_paths:
        node = tree
        for part in path.split("/"):
            node = node.setdefault(part, {})

    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        for name in sorted(node):
            lines.append("  " * depth + name)
            walk(node[name], depth + 1)

    walk(tree, 0)
    return "\n".join(lines)


async def detect_pattern(
    llm: LLMProvider, dependency_graph: dict, code_units: list[CodeUnit], entry_points: list[dict]
) -> PatternClaimResult | None:
    file_paths = [node["id"] for node in dependency_graph.get("nodes", []) if node.get("kind") == "file"]
    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}

    template = load_prompt("pattern_detector")
    input_text = template.render_input(
        dependency_graph_json=json.dumps(dependency_graph),
        directory_tree=_render_directory_tree(file_paths),
        entry_points_json=json.dumps(entry_points),
    )
    response = await llm.complete(
        system=template.system,
        messages=[Message(role="user", content=input_text)],
        response_schema=PatternClaimOutput,
    )
    output = response.parsed
    assert isinstance(output, PatternClaimOutput)

    evidence: list[dict] = []
    for item in output.evidence:
        citations = []
        seen_paths: set[str] = set()
        for raw_path in item.supporting_paths:
            for path in _candidate_paths(raw_path):
                if path in seen_paths:
                    continue
                module_unit = module_units_by_path.get(path)
                if module_unit is None:
                    # LLM named a path that isn't a known file — dropped, not persisted
                    # with a fabricated range (same policy as dependency_graph.py's
                    # unresolved imports: drop rather than guess).
                    continue
                seen_paths.add(path)
                citations.append(
                    {"file_path": path, "line_start": module_unit.line_start, "line_end": module_unit.line_end}
                )
        if not citations:
            # Every supporting_path was unresolvable — persisting this claim
            # would leave an entirely uncited assertion, violating Ground
            # Rule #3 (found via Codex's Phase 2 pre-push review). Drop the
            # item rather than keep it with an empty citations list.
            continue
        evidence.append({"claim": item.claim, "supporting_paths": item.supporting_paths, "citations": citations})

    if not evidence:
        # Every evidence item lost its citations — persisting primary_pattern
        # anyway would leave the whole architectural claim uncited, not just
        # one item of it (found via Codex's Phase 2 pre-push review). No
        # claim is better than an ungrounded one; orchestrator.py skips
        # persisting a PatternClaim row when this is None.
        return None

    return PatternClaimResult(
        primary_pattern=output.primary_pattern,
        confidence=output.confidence,
        evidence=evidence,
        caveats=output.caveats,
        prompt_version=template.version,
        model=response.model,
    )
