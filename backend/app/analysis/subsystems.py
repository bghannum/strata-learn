"""Partitions a snapshot's files into subsystems: the missing layer between
"one file" and "the whole repo" (#53).

Every artifact the pipeline produced before this was keyed to a single file —
module summaries, glossary entries, deep dives, even trade-off decision points
(tradeoff_extractor labels a decision by its file path). Nothing represented a
group of files that does one job together, so nothing could explain one, and
the generated study guide read like an annotated code index.

LAYER A — the partition here is deterministic and derived from directory
structure plus the Layer A dependency graph. ADR-006 permits an LLM to add
"labels/grouping" on top of ground truth; semantics/subsystem_namer.py is where
that happens. Membership is decided here and never by the model.

## Why directories decide membership and the graph only decides order

Graph community detection would be the more sophisticated way to choose
membership, and it is the wrong tool here: its output flips on small edge
changes, so adding one import could reshuffle a repo's subsystems between two
re-indexes of nearly identical code. Phase 7 adds snapshot diffing on top of
these artifacts, where that churn would show up as architectural change that
never happened. Directory structure is the partition the repo's own authors
already committed to, and it is stable by construction.

The graph is used for something it *is* reliable at: ordering subsystems by how
far they sit from an entry point, so a reader moves from the outside in rather
than alphabetically.
"""

from dataclasses import dataclass

# A directory holding fewer files than this isn't a subsystem, it's a folder —
# it merges upward into its parent. Without this, a repo with one file per
# directory would produce one "subsystem" per file, which is the per-file
# framing this module exists to escape.
MIN_SUBSYSTEM_FILES = 3

# Upper bound on subsystems, enforced by merging the smallest upward. A study
# guide with 40 top-level sections is a table of contents, not an explanation,
# and every subsystem costs tokens in the narrative prompt that consumes these.
#
# Deliberately generous rather than tight: merging into a parent makes that
# parent bigger, so an aggressive cap cascades — dogfooding against this repo at
# a cap of 12 collapsed analysis/, api/, db/, generation/, ingestion/ and
# worker/ into a single 26-file "backend/app" blob, which is the whole-repo
# framing subsystems exist to break up. A cap this size only ever fires on repos
# that genuinely have more parts than a reader can hold at once.
MAX_SUBSYSTEMS = 20

ROOT_KEY = "(root)"

# Sorts unreachable subsystems last without a magic sentinel leaking into the
# dataclass's public meaning — "no path from any entry point" is a real state
# (a utility package nothing imports yet, a test-only tree).
UNREACHABLE = 1 << 30


@dataclass(frozen=True)
class SubsystemPartition:
    key: str  # the directory prefix that defines it, or ROOT_KEY
    file_paths: tuple[str, ...]
    depth: int  # edges from the nearest entry point; UNREACHABLE if none reaches it


def _parent_key(key: str) -> str:
    if key == ROOT_KEY or "/" not in key:
        return ROOT_KEY
    return key.rsplit("/", 1)[0]


def _directory_key(file_path: str) -> str:
    if "/" not in file_path:
        return ROOT_KEY
    return file_path.rsplit("/", 1)[0]


def _merge_small_groups(groups: dict[str, list[str]]) -> None:
    """Fold any group under MIN_SUBSYSTEM_FILES into its parent directory,
    deepest first so a chain of thin nested directories collapses in one pass
    rather than one level per iteration."""
    while True:
        candidates = [
            key
            for key, paths in groups.items()
            if key != ROOT_KEY and len(paths) < MIN_SUBSYSTEM_FILES
        ]
        if not candidates:
            return
        # Deepest first, then alphabetical — both only for determinism; the end
        # state is the same set of groups either way.
        key = sorted(candidates, key=lambda k: (-k.count("/"), k))[0]
        groups.setdefault(_parent_key(key), []).extend(groups.pop(key))


def _enforce_max(groups: dict[str, list[str]]) -> None:
    """Merge the smallest groups upward until at most MAX_SUBSYSTEMS remain.
    Groups already at the root can't merge any further, so the loop stops rather
    than spinning when everything has bottomed out."""
    while len(groups) > MAX_SUBSYSTEMS:
        mergeable = [key for key in groups if key != ROOT_KEY]
        if not mergeable:
            return
        key = sorted(mergeable, key=lambda k: (len(groups[k]), -k.count("/"), k))[0]
        groups.setdefault(_parent_key(key), []).extend(groups.pop(key))


def _entry_point_depths(file_ids: set[str], edges: list[dict], entry_points: list[dict]) -> dict[str, int]:
    """Breadth-first distance from the entry-point files, following imports in
    the direction they're written (importer -> imported). An entry point is
    depth 0, what it imports is 1, and so on — so depth reads as "how far in
    from the outside of the system this is"."""
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if source in file_ids and target in file_ids:
            adjacency.setdefault(source, []).append(target)

    frontier = sorted({ep["file"] for ep in entry_points if ep.get("file") in file_ids})
    depths = {path: 0 for path in frontier}
    distance = 0
    while frontier:
        distance += 1
        next_frontier: list[str] = []
        for path in frontier:
            for neighbor in sorted(adjacency.get(path, [])):
                if neighbor not in depths:
                    depths[neighbor] = distance
                    next_frontier.append(neighbor)
        frontier = next_frontier
    return depths


def partition_subsystems(dependency_graph: dict, entry_points: list[dict]) -> list[SubsystemPartition]:
    """Group every file node into exactly one subsystem, ordered outside-in.

    Exactly one: a file belonging to two subsystems would make "which section
    does this deep dive live under" ambiguous, and buys nothing at single-repo
    scale.
    """
    nodes = dependency_graph.get("nodes", [])
    file_ids = sorted(n["id"] for n in nodes if n.get("kind") == "file")
    if not file_ids:
        return []

    groups: dict[str, list[str]] = {}
    for file_path in file_ids:
        groups.setdefault(_directory_key(file_path), []).append(file_path)

    _merge_small_groups(groups)
    _enforce_max(groups)

    depths = _entry_point_depths(set(file_ids), dependency_graph.get("edges", []), entry_points)

    partitions = [
        SubsystemPartition(
            key=key,
            file_paths=tuple(sorted(paths)),
            # A subsystem is as close to the outside as its closest file: one
            # entry-point file makes the whole group part of the system's edge.
            depth=min((depths.get(p, UNREACHABLE) for p in paths), default=UNREACHABLE),
        )
        for key, paths in groups.items()
    ]
    # Nesting depth breaks depth ties before the alphabetical fallback, so the
    # order still reads outside-in when the graph can't help: a repo whose
    # imports don't resolve to file nodes (a monorepo indexed from its root,
    # where Python absolute imports name a package root one directory down)
    # leaves every subsystem UNREACHABLE, and "frontend" before "frontend/src"
    # before "frontend/src/components/ui" is a far better reading order than
    # pure alphabetical. The alphabetical key remains last so re-indexing
    # identical code always produces the same order (Phase 7 diffs these).
    return sorted(partitions, key=lambda p: (p.depth, p.key.count("/"), p.key))
