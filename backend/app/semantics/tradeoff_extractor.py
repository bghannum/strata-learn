"""Implements docs/prompts/tradeoff_extractor.v1.md (docs/design/original-project-plan.md §9.3) —
the product's differentiator. Two parts: `identify_decision_points` picks
which files are worth asking about (a deterministic heuristic — the spec
doesn't pin this down, see the module docstring below), and
`extract_tradeoffs` runs one LLM call per candidate, reading the real code
from the still-on-disk source directory (CodeUnit only stores signatures, not
full bodies).

Every `evidence_ref` the LLM returns is validated against known CodeUnit line
spans before being persisted (Ground Rule #3) — an invalid ref is dropped, not
kept with a fabricated range. The originating decision point's own citation is
always prepended to evidence_refs, so every card has at least one citation
that's true by construction, independent of what the LLM did.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.db.models import CodeUnit, UnitType
from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt

# JUDGMENT CALL — docs/design/original-project-plan.md doesn't specify how to identify "decision
# points" to feed the trade-off extractor. This heuristic combines (a) files
# with high fan-in+fan-out in the dependency graph and (b) files importing a
# known "infra" package (queue/cache/HTTP/DB libraries — the kind of import
# that usually reflects a real architectural choice). Expect to retune the
# package list and scoring against real repos at the Phase 2 checkpoint.
INFRA_PACKAGES = {"arq", "celery", "redis", "kafka", "sqlalchemy", "httpx", "requests", "boto3"}


@dataclass(frozen=True)
class DecisionPointCandidate:
    label: str
    file_path: str
    line_start: int
    line_end: int
    rationale: str


def identify_decision_points(
    dependency_graph: dict, code_units: list[CodeUnit], entry_points: list[dict], *, limit: int = 8
) -> list[DecisionPointCandidate]:
    file_node_ids = {node["id"] for node in dependency_graph.get("nodes", []) if node.get("kind") == "file"}

    fan_count: dict[str, int] = dict.fromkeys(file_node_ids, 0)
    infra_imports: dict[str, set[str]] = {}

    for edge in dependency_graph.get("edges", []):
        source, target, kind = edge["source"], edge["target"], edge["kind"]
        if source in fan_count:
            fan_count[source] += 1
        if target in fan_count:
            fan_count[target] += 1
        if kind == "imports_external" and source in file_node_ids and target.startswith("external:"):
            package = target.removeprefix("external:")
            if package in INFRA_PACKAGES:
                infra_imports.setdefault(source, set()).add(package)

    # Infra-package hits are boosted above pure fan-in/out scores — they're a
    # stronger signal of an actual design decision than connectivity alone.
    scored: list[tuple[int, str, str]] = []
    for file_path in file_node_ids:
        if file_path in infra_imports:
            packages = ", ".join(sorted(infra_imports[file_path]))
            scored.append((fan_count[file_path] + 100, file_path, f"imports infra package(s): {packages}"))
        elif fan_count[file_path] > 0:
            scored.append((fan_count[file_path], file_path, f"fan-in+fan-out={fan_count[file_path]}"))

    scored.sort(key=lambda t: (-t[0], t[1]))

    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}

    candidates: list[DecisionPointCandidate] = []
    for _score, file_path, rationale in scored[:limit]:
        module_unit = module_units_by_path.get(file_path)
        if module_unit is None:
            continue
        candidates.append(
            DecisionPointCandidate(
                label=file_path,
                file_path=file_path,
                line_start=module_unit.line_start,
                line_end=module_unit.line_end,
                rationale=rationale,
            )
        )
    return candidates


class EvidenceRef(BaseModel):
    file_path: str
    line_start: int
    line_end: int


class TradeoffCardOutput(BaseModel):
    decision: str
    alternatives_considered: list[str]
    likely_reasoning: str
    tradeoff_cost: str
    confidence: Literal["high", "medium", "low"]
    evidence_refs: list[EvidenceRef]


@dataclass(frozen=True)
class TradeoffCardResult:
    decision: str
    alternatives_considered: list[str]
    likely_reasoning: str
    tradeoff_cost: str
    confidence: str
    evidence_refs: list[dict]
    prompt_version: str
    model: str


# A candidate's line_start..line_end is the module unit's full line range —
# i.e. the whole file — so this was only bounded indirectly by Layer A's 1
# MiB max_file_size_bytes, a cap sized for parsing, not for LLM context
# limits. A dense, low-comment file well under 1 MiB can still run tens of
# thousands of tokens (found via Codex's PR #17 review, #20). Truncated
# snippets are marked explicitly in the rendered text so the model doesn't
# treat a cut file as complete.
MAX_SNIPPET_CHARS = 20_000


def _read_snippet(source_dir: Path, file_path: str, line_start: int, line_end: int) -> str:
    # errors="replace", matching parser.py's decoding policy exactly: Layer A
    # parses on raw bytes and never raises on non-UTF-8 source, so a file that
    # parsed successfully must not fail Layer B just because Path.read_text()'s
    # strict-UTF-8 default raises UnicodeDecodeError (found via Codex's
    # Phase 2 pre-push review).
    text = (source_dir / file_path).read_bytes().decode("utf-8", errors="replace")
    lines = text.splitlines()
    snippet = "\n".join(lines[line_start - 1 : line_end])
    if len(snippet) > MAX_SNIPPET_CHARS:
        snippet = snippet[:MAX_SNIPPET_CHARS] + f"\n... [truncated: file exceeds {MAX_SNIPPET_CHARS} chars]"
    return snippet


def _context_snippet(file_path: str, dependency_graph: dict) -> str:
    edges = dependency_graph.get("edges", [])
    importers = sorted({e["source"] for e in edges if e["target"] == file_path})
    imported = sorted({e["target"] for e in edges if e["source"] == file_path and not e["target"].startswith("external:")})

    lines = []
    if importers:
        lines.append("Imported by: " + ", ".join(importers))
    if imported:
        lines.append("Imports: " + ", ".join(imported))
    return "\n".join(lines) if lines else "(no other repo files import or are imported by this file)"


def _valid_ref(ref: EvidenceRef, candidate_file_path: str, module_units_by_path: dict[str, CodeUnit]) -> bool:
    if ref.file_path != candidate_file_path:
        # The model only ever sees the *candidate's own* file content
        # (code_snippet) — importer/imported files named in context_snippet
        # never have their content shown, so a citation against one of them
        # is an unverifiable guess, not a grounded reference, even if the
        # line range happens to fall within that file's real bounds (found
        # via Codex's Phase 2 pre-push review).
        return False
    module_unit = module_units_by_path.get(ref.file_path)
    if module_unit is None:
        return False
    return 1 <= ref.line_start <= ref.line_end <= module_unit.line_end


async def extract_tradeoffs(
    llm: LLMProvider,
    candidates: list[DecisionPointCandidate],
    source_dir: Path,
    dependency_graph: dict,
    code_units: list[CodeUnit],
) -> list[TradeoffCardResult]:
    template = load_prompt("tradeoff_extractor")
    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}
    results: list[TradeoffCardResult] = []

    for candidate in candidates:
        input_text = template.render_input(
            decision_description=f"{candidate.label} ({candidate.rationale})",
            code_snippet=_read_snippet(source_dir, candidate.file_path, candidate.line_start, candidate.line_end),
            context_snippet=_context_snippet(candidate.file_path, dependency_graph),
        )
        response = await llm.complete(
            system=template.system,
            messages=[Message(role="user", content=input_text)],
            response_schema=TradeoffCardOutput,
        )
        output = response.parsed
        assert isinstance(output, TradeoffCardOutput)

        validated_refs = [
            ref.model_dump()
            for ref in output.evidence_refs
            if _valid_ref(ref, candidate.file_path, module_units_by_path)
        ]
        if not validated_refs:
            # The seed citation only explains *why* this file was flagged as a
            # decision point (the deterministic heuristic) — it doesn't
            # substantiate the LLM's actual decision/reasoning/cost claims.
            # Persisting the card with only the seed would look grounded
            # without actually being grounded (found via Codex's Phase 2
            # pre-push review). Drop the card rather than fake it.
            continue

        seed_citation = {
            "file_path": candidate.file_path,
            "line_start": candidate.line_start,
            "line_end": candidate.line_end,
        }
        evidence_refs = [seed_citation, *validated_refs]

        results.append(
            TradeoffCardResult(
                decision=output.decision,
                alternatives_considered=output.alternatives_considered,
                likely_reasoning=output.likely_reasoning,
                tradeoff_cost=output.tradeoff_cost,
                confidence=output.confidence,
                evidence_refs=evidence_refs,
                prompt_version=template.version,
                model=response.model,
            )
        )

    return results
