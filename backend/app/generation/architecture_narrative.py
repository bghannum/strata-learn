"""Implements docs/prompts/architecture_narrative.v1.md: the one synthesis pass
in study-guide assembly (#52).

Before this, `_build_architecture` was pure string templating over PatternClaim's
persisted fields — a pattern label, a confidence, and a bulleted evidence list.
There was no narrative anywhere in the study guide to make better, which is why
the section read as citation-heavy: a citable claim was the only unit it could
produce. This module adds the missing step, and study_guide_builder renders its
output above the evidence rather than instead of it.

Citations are attached *after* drafting: the model writes the explanation, then
names which of the files it was given back each part, and those paths are
resolved here against real CodeUnit line ranges. A path that doesn't resolve is
dropped rather than persisted with a fabricated range — the same policy
pattern_detector.py and dependency_graph.py already follow.
"""

import json
import logging
from dataclasses import dataclass

from pydantic import BaseModel

from app.db.models import CodeUnit, PatternClaim, Subsystem, TradeoffCard, UnitType
from app.semantics.llm_provider import LLMOutputError, LLMProvider, Message, require_parsed
from app.semantics.prompts import load_prompt

logger = logging.getLogger(__name__)

# Bounds on what reaches the prompt. The persisted inputs are each bounded by
# their own producer (MAX_SUBSYSTEMS, identify_decision_points' limit,
# MAX_ENTRY_POINTS), but nothing bounds them *in combination*, and this call
# uses the strongest, most expensive model tier.
MAX_FILES_PER_SUBSYSTEM_IN_PROMPT = 25
MAX_ENTRY_POINTS_IN_PROMPT = 30
MAX_TRADEOFF_CARDS_IN_PROMPT = 12


class WhySection(BaseModel):
    heading: str
    body: str
    supporting_paths: list[str] = []


class ArchitectureNarrativeOutput(BaseModel):
    overview: str
    why_sections: list[WhySection] = []


@dataclass(frozen=True)
class NarrativeCitation:
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str


@dataclass(frozen=True)
class NarrativeSection:
    heading: str
    body: str


@dataclass(frozen=True)
class ArchitectureNarrative:
    overview: str
    why_sections: list[NarrativeSection]
    citations: list[NarrativeCitation]
    prompt_version: str
    model: str


def narrative_payload(narrative: "ArchitectureNarrative") -> dict:
    """Cache representation (artifact_cache.py). Explicit rather than
    dataclasses.asdict so the persisted shape is a deliberate contract — a
    field renamed here would otherwise silently stop rehydrating."""
    return {
        "overview": narrative.overview,
        "why_sections": [{"heading": s.heading, "body": s.body} for s in narrative.why_sections],
        "citations": [
            {
                "file_path": c.file_path,
                "line_start": c.line_start,
                "line_end": c.line_end,
                "claim_excerpt": c.claim_excerpt,
            }
            for c in narrative.citations
        ],
    }


def narrative_from_payload(payload: dict, prompt_version: str, model: str) -> "ArchitectureNarrative":
    return ArchitectureNarrative(
        overview=payload.get("overview", ""),
        why_sections=[
            NarrativeSection(heading=s["heading"], body=s["body"]) for s in payload.get("why_sections", [])
        ],
        citations=[
            NarrativeCitation(
                file_path=c["file_path"],
                line_start=c["line_start"],
                line_end=c["line_end"],
                claim_excerpt=c["claim_excerpt"],
            )
            for c in payload.get("citations", [])
        ],
        prompt_version=prompt_version,
        model=model,
    )


def _pattern_summary(pattern_claim: PatternClaim | None) -> str:
    if pattern_claim is None:
        return "none detected"
    parts = [f"{pattern_claim.primary_pattern} (confidence: {pattern_claim.confidence.value})"]
    if pattern_claim.caveats:
        parts.append(f"Caveats: {pattern_claim.caveats}")
    evidence = [item.get("claim", "") for item in pattern_claim.evidence]
    if evidence:
        parts.append("Evidence: " + "; ".join(e for e in evidence if e))
    return " ".join(parts)


def _subsystems_payload(subsystems: list[Subsystem]) -> list[dict]:
    return [
        {
            "name": s.name,
            "role": s.role,
            # The key is deliberately included: the model is told to name
            # subsystems by name in its prose, but its supporting_paths have to
            # be real repository paths, and seeing the directory makes the
            # relationship between the two obvious.
            "directory": s.key,
            "files": list(s.file_paths[:MAX_FILES_PER_SUBSYSTEM_IN_PROMPT]),
        }
        for s in sorted(subsystems, key=lambda s: s.order)
    ]


def _tradeoffs_payload(cards: list[TradeoffCard]) -> list[dict]:
    ordered = sorted(cards, key=lambda c: c.decision)[:MAX_TRADEOFF_CARDS_IN_PROMPT]
    return [
        {
            "decision": c.decision,
            "likely_reasoning": c.likely_reasoning,
            "tradeoff_cost": c.tradeoff_cost,
            "alternatives_considered": c.alternatives_considered,
            "evidence_paths": sorted({ref["file_path"] for ref in c.evidence_refs}),
        }
        for c in ordered
    ]


async def build_architecture_narrative(
    llm: LLMProvider,
    pattern_claim: PatternClaim | None,
    subsystems: list[Subsystem],
    tradeoff_cards: list[TradeoffCard],
    entry_points: list[dict],
    code_units: list[CodeUnit],
) -> ArchitectureNarrative | None:
    if pattern_claim is None and not subsystems and not tradeoff_cards:
        # Nothing to synthesize from. Returning None keeps the old
        # "_No architecture pattern could be grounded in evidence._" path
        # intact rather than spending the strongest model tier on an empty
        # prompt to produce confident-sounding prose about nothing.
        return None

    template = load_prompt("architecture_narrative")
    input_text = template.render_input(
        pattern_summary=_pattern_summary(pattern_claim),
        subsystems_json=json.dumps(_subsystems_payload(subsystems)),
        entry_points_json=json.dumps(
            sorted(entry_points, key=lambda ep: (ep["file"], ep["kind"]))[:MAX_ENTRY_POINTS_IN_PROMPT]
        ),
        tradeoffs_json=json.dumps(_tradeoffs_payload(tradeoff_cards)),
    )
    response = await llm.complete(
        system=template.system,
        messages=[Message(role="user", content=input_text)],
        response_schema=ArchitectureNarrativeOutput,
    )
    try:
        output = require_parsed(response, ArchitectureNarrativeOutput)
    except LLMOutputError as exc:
        # Unlike subsystem names or diagram labels, there is no deterministic
        # substitute here — the narrative *is* the model's prose. None is
        # already the declared return for "no narrative", so the study guide
        # renders without the why-sections rather than not at all.
        logger.warning("No architecture narrative for this snapshot: %s", exc)
        return None

    module_units_by_path = {u.file_path: u for u in code_units if u.unit_type == UnitType.module}

    sections: list[NarrativeSection] = []
    citations: list[NarrativeCitation] = []
    for item in output.why_sections:
        heading = item.heading.strip()
        body = item.body.strip()
        if not heading or not body:
            continue
        sections.append(NarrativeSection(heading=heading, body=body))
        # claim_excerpt names both heading and body: they're one claim made by
        # one call, and a citation lookup by excerpt should cover the whole
        # rendered block rather than only its title (the same reasoning
        # study_guide_builder already applies to trade-off cards and
        # deep-dive summaries).
        claim_excerpt = f"{heading} — {body}"
        seen: set[str] = set()
        for raw_path in item.supporting_paths:
            module_unit = module_units_by_path.get(raw_path)
            if module_unit is None or raw_path in seen:
                continue  # unknown path — dropped, never persisted with a fabricated range
            seen.add(raw_path)
            citations.append(
                NarrativeCitation(
                    file_path=raw_path,
                    line_start=module_unit.line_start,
                    line_end=module_unit.line_end,
                    claim_excerpt=claim_excerpt,
                )
            )

    overview = output.overview.strip()
    if not overview and not sections:
        return None

    return ArchitectureNarrative(
        overview=overview,
        why_sections=sections,
        citations=citations,
        prompt_version=template.version,
        model=response.model,
    )
