"""Builds a Mermaid component diagram from the dependency graph (LAYER A —
edges are 100% deterministic, see dependency_graph.py's own docstring: this
module only ever *labels* what's there, never invents a relationship). The
one new LLM call in Phase 3: short human-readable labels for the diagram's
nodes (docs/prompts/diagram_labeler.v1.md), grounded to the file paths
actually kept in the diagram — a file the LLM forgets to label still gets a
deterministic fallback (its basename) so the diagram is always complete and
valid Mermaid syntax regardless of the LLM's output.
"""

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel

from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt

# Component diagrams are for human eyes, not an LLM context window — capped
# far smaller than pattern_detector.py's MAX_GRAPH_NODES/EDGES (500/1000),
# which only needs to stay under a model's context limit. A diagram with
# hundreds of boxes isn't readable regardless of what produced it.
MAX_DIAGRAM_NODES = 30
MAX_DIAGRAM_EDGES = 60

_UNSAFE_LABEL_CHARS = re.compile(r'["\[\]{}]')
# A structured LLM response isn't prevented from containing an embedded
# newline/tab in a label field — left intact, it splits a Mermaid node
# declaration across lines, corrupting the diagram's syntax (found via
# Codex's Phase 3 pre-push review). Collapsed to a single space before the
# char-strip above runs.
_WHITESPACE = re.compile(r"\s+")


class DiagramLabelItem(BaseModel):
    file_path: str
    label: str


class DiagramLabelOutput(BaseModel):
    labels: list[DiagramLabelItem]


@dataclass(frozen=True)
class DiagramResult:
    mermaid: str
    file_paths: list[str]  # files included, so the caller can attach citations to them
    labels: dict[str, str]  # resolved label per file_path (LLM or fallback) — the actual
    # rendered claim about that file, so the caller's citation can name what
    # it supports instead of just "included in the diagram" (found via
    # Codex's Phase 3 pre-push review).
    prompt_version: str
    model: str


def _select_nodes(dependency_graph: dict) -> tuple[list[str], list[dict]]:
    """Highest fan-in+fan-out files are the most architecturally significant —
    the same connectivity heuristic tradeoff_extractor.identify_decision_points
    uses to pick decision points, applied here to pick diagram components
    instead. External packages are left out entirely: a component diagram
    shows this repo's own structure, not its dependency list."""
    all_nodes = dependency_graph.get("nodes", [])
    all_edges = dependency_graph.get("edges", [])
    file_node_ids = {n["id"] for n in all_nodes if n.get("kind") == "file"}

    fan_count: dict[str, int] = dict.fromkeys(file_node_ids, 0)
    internal_edges = []
    for edge in all_edges:
        source, target = edge["source"], edge["target"]
        if source in file_node_ids and target in file_node_ids:
            fan_count[source] += 1
            fan_count[target] += 1
            internal_edges.append(edge)

    # Only files that actually import or are imported by another file in this
    # repo count as "components" of a component diagram — an isolated file
    # with fan_count 0 (e.g. the only file in a single-file repo) has no
    # relationship to draw, so it's excluded rather than rendered as an
    # unconnected box (and, more importantly, isn't worth spending the one
    # LLM call in this module labeling).
    connected = [p for p in file_node_ids if fan_count[p] > 0]
    kept = sorted(connected, key=lambda p: (-fan_count[p], p))[:MAX_DIAGRAM_NODES]
    kept_set = set(kept)
    edges = [e for e in internal_edges if e["source"] in kept_set and e["target"] in kept_set]
    edges = sorted(edges, key=lambda e: (e["source"], e["target"]))[:MAX_DIAGRAM_EDGES]
    return sorted(kept), edges


def _fallback_label(file_path: str) -> str:
    basename = file_path.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0]
    return stem.replace("_", " ").title()


def _sanitize_label(label: str) -> str:
    # Mermaid node syntax is `id["label"]` — an unescaped quote or bracket in
    # the label breaks the diagram's syntax, not just its readability, and so
    # does an embedded newline splitting the declaration across lines.
    label = _WHITESPACE.sub(" ", label)
    return _UNSAFE_LABEL_CHARS.sub("", label).strip() or "Unnamed"


async def build_component_diagram(
    llm: LLMProvider, dependency_graph: dict, module_purposes: dict[str, str]
) -> DiagramResult | None:
    file_paths, edges = _select_nodes(dependency_graph)
    if not file_paths:
        # No internal file-to-file edges to diagram (e.g. a single-file repo,
        # or one where every import is external) — nothing to render.
        return None

    template = load_prompt("diagram_labeler")
    files_payload = [{"file_path": p, "purpose": module_purposes.get(p)} for p in file_paths]
    input_text = template.render_input(files_json=json.dumps(files_payload))
    response = await llm.complete(
        system=template.system,
        messages=[Message(role="user", content=input_text)],
        response_schema=DiagramLabelOutput,
    )
    output = response.parsed
    assert isinstance(output, DiagramLabelOutput)

    kept_set = set(file_paths)
    raw_labels = {item.file_path: item.label for item in output.labels if item.file_path in kept_set}
    node_ids = {path: f"n{i}" for i, path in enumerate(file_paths)}

    lines = ["flowchart TD"]
    resolved_labels: dict[str, str] = {}
    for path in file_paths:
        label = _sanitize_label(raw_labels.get(path) or _fallback_label(path))
        resolved_labels[path] = label
        lines.append(f'    {node_ids[path]}["{label}"]')
    for edge in edges:
        lines.append(f"    {node_ids[edge['source']]} --> {node_ids[edge['target']]}")

    return DiagramResult(
        mermaid="\n".join(lines),
        file_paths=file_paths,
        labels=resolved_labels,
        prompt_version=template.version,
        model=response.model,
    )
