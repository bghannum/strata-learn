"""Architectural diff between two snapshots of the same repository (#63).

Re-indexing produces a *second study guide*, not a delta. For a learning tool
that's backwards: once you already understand a system, what you need on the
next pass is what changed, not another full description of everything.

Pure functions over already-persisted rows — no DB access, no LLM calls. An
LLM summary of a diff is easy to add on top of a correct diff and impossible to
trust on top of a wrong one, so this produces structure only.

## Why matching is structural, never textual

Two runs of the same prompt over identical code produce differently-worded
trade-off cards. Diffing generated prose directly would report churn that isn't
real — the single biggest way a feature like this becomes noise nobody reads.

So nothing here compares generated text to decide whether something *is* the
same thing. Subsystems match on their key (stable by construction —
analysis/subsystems.py), trade-off cards match on the set of files their
evidence cites, and dependency edges match on subsystem keys. Text is only ever
compared *after* two things have been matched structurally, to report that the
reasoning changed.

A card whose citations moved shows up as removed-plus-added rather than
changed. That's honest: its grounding genuinely changed, and claiming otherwise
would mean guessing.
"""

from dataclasses import dataclass, field

from app.db.models import PatternClaim, Subsystem, TradeoffCard


@dataclass(frozen=True)
class SubsystemRef:
    key: str
    name: str


@dataclass(frozen=True)
class SubsystemMembershipChange:
    key: str
    name: str
    files_added: list[str]
    files_removed: list[str]


@dataclass(frozen=True)
class SubsystemDiff:
    added: list[SubsystemRef] = field(default_factory=list)
    removed: list[SubsystemRef] = field(default_factory=list)
    changed: list[SubsystemMembershipChange] = field(default_factory=list)


@dataclass(frozen=True)
class TradeoffChange:
    decision_before: str
    decision_after: str
    reasoning_before: str
    reasoning_after: str
    cost_before: str
    cost_after: str
    evidence_paths: list[str]


@dataclass(frozen=True)
class TradeoffDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[TradeoffChange] = field(default_factory=list)


@dataclass(frozen=True)
class PatternDiff:
    changed: bool
    pattern_before: str | None
    pattern_after: str | None
    confidence_before: str | None
    confidence_after: str | None


@dataclass(frozen=True)
class SubsystemEdge:
    source: str
    target: str


@dataclass(frozen=True)
class DependencyDiff:
    edges_added: list[SubsystemEdge] = field(default_factory=list)
    edges_removed: list[SubsystemEdge] = field(default_factory=list)


def diff_subsystems(before: list[Subsystem], after: list[Subsystem]) -> SubsystemDiff:
    by_key_before = {s.key: s for s in before}
    by_key_after = {s.key: s for s in after}

    added = [SubsystemRef(key=k, name=by_key_after[k].name) for k in sorted(by_key_after.keys() - by_key_before.keys())]
    removed = [
        SubsystemRef(key=k, name=by_key_before[k].name) for k in sorted(by_key_before.keys() - by_key_after.keys())
    ]

    changed: list[SubsystemMembershipChange] = []
    for key in sorted(by_key_before.keys() & by_key_after.keys()):
        files_before = set(by_key_before[key].file_paths)
        files_after = set(by_key_after[key].file_paths)
        if files_before == files_after:
            continue
        changed.append(
            SubsystemMembershipChange(
                key=key,
                # The *after* name: a diff describes the current state, and the
                # generated name can drift between runs even when membership
                # didn't. Reporting a name change as an architectural change is
                # exactly the noise this module avoids, so the name is carried
                # for display only and never compared.
                name=by_key_after[key].name,
                files_added=sorted(files_after - files_before),
                files_removed=sorted(files_before - files_after),
            )
        )

    return SubsystemDiff(added=added, removed=removed, changed=changed)


def _evidence_key(card: TradeoffCard) -> frozenset[str]:
    """The set of files a card's evidence cites — its structural identity. Two
    cards about the same decision over unchanged code cite the same files even
    when the prose differs, which is what makes this usable as a match key."""
    return frozenset(ref["file_path"] for ref in card.evidence_refs)


def diff_tradeoffs(before: list[TradeoffCard], after: list[TradeoffCard]) -> TradeoffDiff:
    # A repo can produce two cards citing the same file set; keeping the first
    # by decision label makes the pairing deterministic rather than dependent
    # on row order.
    by_key_before = {}
    for card in sorted(before, key=lambda c: c.decision):
        by_key_before.setdefault(_evidence_key(card), card)
    by_key_after = {}
    for card in sorted(after, key=lambda c: c.decision):
        by_key_after.setdefault(_evidence_key(card), card)

    added = sorted(by_key_after[k].decision for k in by_key_after.keys() - by_key_before.keys())
    removed = sorted(by_key_before[k].decision for k in by_key_before.keys() - by_key_after.keys())

    changed: list[TradeoffChange] = []
    for key in by_key_before.keys() & by_key_after.keys():
        old, new = by_key_before[key], by_key_after[key]
        if (old.likely_reasoning, old.tradeoff_cost, old.decision) == (
            new.likely_reasoning,
            new.tradeoff_cost,
            new.decision,
        ):
            continue
        changed.append(
            TradeoffChange(
                decision_before=old.decision,
                decision_after=new.decision,
                reasoning_before=old.likely_reasoning,
                reasoning_after=new.likely_reasoning,
                cost_before=old.tradeoff_cost,
                cost_after=new.tradeoff_cost,
                evidence_paths=sorted(key),
            )
        )
    changed.sort(key=lambda c: c.decision_after)

    return TradeoffDiff(added=added, removed=removed, changed=changed)


def diff_pattern(before: PatternClaim | None, after: PatternClaim | None) -> PatternDiff:
    pattern_before = before.primary_pattern if before is not None else None
    pattern_after = after.primary_pattern if after is not None else None
    confidence_before = before.confidence.value if before is not None else None
    confidence_after = after.confidence.value if after is not None else None
    return PatternDiff(
        changed=(pattern_before, confidence_before) != (pattern_after, confidence_after),
        pattern_before=pattern_before,
        pattern_after=pattern_after,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
    )


def _subsystem_edges(dependency_graph: dict, subsystems: list[Subsystem]) -> set[tuple[str, str]]:
    """Projects file-to-file edges up to subsystem-to-subsystem.

    A raw file-level edge diff is technically accurate and useless to read — a
    refactor that moves twenty files reports forty changes that all say the
    same thing. At subsystem level the same refactor is one line, and "the API
    layer now depends on the worker" is a sentence someone can act on.

    External packages are kept as their own pseudo-subsystem, because gaining
    or losing a dependency on Redis is exactly the kind of architectural change
    worth surfacing.
    """
    subsystem_by_file = {path: s.key for s in subsystems for path in s.file_paths}

    edges: set[tuple[str, str]] = set()
    for edge in dependency_graph.get("edges", []):
        source = subsystem_by_file.get(edge["source"])
        if source is None:
            continue  # a file in no subsystem — nothing meaningful to attribute it to
        target_raw = edge["target"]
        target = target_raw if target_raw.startswith("external:") else subsystem_by_file.get(target_raw)
        if target is None or source == target:
            continue  # internal to one subsystem — not an architectural relationship
        edges.add((source, target))
    return edges


def diff_dependencies(
    graph_before: dict, subsystems_before: list[Subsystem], graph_after: dict, subsystems_after: list[Subsystem]
) -> DependencyDiff:
    edges_before = _subsystem_edges(graph_before, subsystems_before)
    edges_after = _subsystem_edges(graph_after, subsystems_after)
    return DependencyDiff(
        edges_added=[SubsystemEdge(source=s, target=t) for s, t in sorted(edges_after - edges_before)],
        edges_removed=[SubsystemEdge(source=s, target=t) for s, t in sorted(edges_before - edges_after)],
    )
