"""Implements docs/prompts/pattern_detector.v1.md (docs/design/original-project-plan.md §9.2): one
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

# A hard cap on the dependency graph's size before it's serialized into the
# prompt — found via Codex's Phase 2 pre-push review: ingestion has no
# file-count cap on a git-cloned repo (only zip uploads are capped, at 5,000
# files), so a large repo's full node/edge list could blow the request past
# the model's context limit. Nodes are truncated first, sorted by id for
# determinism (the same subset every run); edges are then filtered to only
# those whose source AND target both survived, so the graph handed to the
# model stays internally consistent rather than referencing dropped nodes.
MAX_GRAPH_NODES = 500
MAX_GRAPH_EDGES = 1000


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


def _bound_graph(dependency_graph: dict) -> dict:
    all_nodes = dependency_graph.get("nodes", [])
    all_edges = dependency_graph.get("edges", [])

    # File nodes get priority over "external:*" package nodes (found via
    # Codex's Phase 2 pre-push review round 8: sorting every node together
    # let external dependency IDs — often alphabetically early, e.g.
    # "external:aiohttp" — crowd real files out of the budget on a
    # dependency-heavy repo, skewing pattern detection toward files that
    # happened to sort late). Files are capped first; any remaining budget
    # goes to external nodes, and only ones actually adjacent to a kept file
    # (real evidence of a dependency relationship), not an arbitrary subset.
    file_nodes = sorted((n for n in all_nodes if n.get("kind") == "file"), key=lambda n: n["id"])
    external_nodes = sorted((n for n in all_nodes if n.get("kind") != "file"), key=lambda n: n["id"])

    kept_file_nodes = file_nodes[:MAX_GRAPH_NODES]
    kept_file_ids = {n["id"] for n in kept_file_nodes}

    remaining_budget = MAX_GRAPH_NODES - len(kept_file_nodes)
    kept_external_nodes: list[dict] = []
    if remaining_budget > 0:
        adjacent_external_ids = {e["target"] for e in all_edges if e["source"] in kept_file_ids} | {
            e["source"] for e in all_edges if e["target"] in kept_file_ids
        }
        kept_external_nodes = [n for n in external_nodes if n["id"] in adjacent_external_ids][:remaining_budget]

    nodes = kept_file_nodes + kept_external_nodes
    kept_ids = kept_file_ids | {n["id"] for n in kept_external_nodes}

    edges = [e for e in all_edges if e["source"] in kept_ids and e["target"] in kept_ids]
    edges = sorted(edges, key=lambda e: (e["source"], e["target"], e["kind"]))[:MAX_GRAPH_EDGES]

    return {"nodes": nodes, "edges": edges}


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
    bounded_graph = _bound_graph(dependency_graph)
    # Directory tree is derived from the same bounded node set, not the raw
    # graph — otherwise it could list files the model's own graph view no
    # longer includes, and the two payloads would disagree with each other.
    file_paths = [node["id"] for node in bounded_graph["nodes"] if node.get("kind") == "file"]
    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}

    template = load_prompt("pattern_detector")
    input_text = template.render_input(
        dependency_graph_json=json.dumps(bounded_graph),
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
        # Persist only the paths that actually resolved (== citations' own
        # file_paths, same order) — not the LLM's raw supporting_paths, which
        # can still contain hallucinated/relationship-notation entries that
        # were silently dropped from citations above (found via Codex's
        # Phase 2 pre-push review: persisting the raw list made it look like
        # those paths were validated when they weren't).
        evidence.append(
            {"claim": item.claim, "supporting_paths": [c["file_path"] for c in citations], "citations": citations}
        )

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
