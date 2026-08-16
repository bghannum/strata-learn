"""Scoring for the voice-backend evaluation. Pure functions, no I/O, no
provider — importable and testable without the `eval` extra. jiwer is
used for the WER *alignment* when present (evaluation/voice/run.py), but
everything that decides what counts as a match lives here, because that's
the part that has to be argued for.

## Why not just jiwer's default WER

Its default transform strips punctuation, which shreds exactly the tokens
under test: "analyze_source" becomes two words, "snapshot.py" becomes two
more, "tree-sitter" becomes two. A transcript that got every identifier
right would score badly, and one that got them all wrong might score
better. So normalization here preserves `_ / . -` inside tokens.

## Why two metrics

WER alone is the wrong measurement for this feature. A transcript can score
0.10 WER while missing the one token the learner actually needed — and
that token is the whole reason vocabulary hints exist. Identifier recall
(the fraction of expected_identifiers present as exact normalized tokens)
is what the report leads with; WER is context.

## Spoken-form aliases

A speaker who says "analyze underscore source" has said the identifier
correctly. Applied *symmetrically* — to reference and hypothesis alike —
so no backend is rewarded or punished for whether it emitted the word or
the symbol.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Spoken forms of the separators an identifier can contain. Symmetric on
# purpose; see the module docstring.
_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\s+underscore\s+"), "_"),
    (re.compile(r"\s+under\s+score\s+"), "_"),
    (re.compile(r"\s+dot\s+"), "."),
    (re.compile(r"\s+slash\s+"), "/"),
    (re.compile(r"\s+forward\s+slash\s+"), "/"),
    (re.compile(r"\s+dash\s+"), "-"),
    (re.compile(r"\s+hyphen\s+"), "-"),
]

# Everything except word characters and the in-token separators we keep.
_STRIP = re.compile(r"[^\w\s_./\-]")
_EDGE_PUNCT = re.compile(r"(^[./\-]+)|([./\-]+$)")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, fold accents, apply spoken-form aliases, drop punctuation
    that isn't part of an identifier, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = f" {text} "
    for pattern, replacement in _ALIASES:
        text = pattern.sub(replacement, text)
    text = _STRIP.sub(" ", text)
    tokens = [_EDGE_PUNCT.sub("", tok) for tok in _SPACES.split(text.strip())]
    return " ".join(tok for tok in tokens if tok)


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance over normalized tokens, divided by the
    reference length. Pure Python; jiwer produces the same number when its
    transforms are set to this normalization, and run.py cross-checks that
    on every clip so the two can't quietly disagree."""
    ref = tokens(reference)
    hyp = tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if r == h else 1)))
        prev = cur
    return prev[-1] / len(ref)


def identifier_recall(expected: list[str], hypothesis: str) -> float | None:
    """Fraction of expected identifiers present as exact normalized tokens
    in the hypothesis. None when there are none to find (a prose clip),
    which the report shows as "—" rather than a misleading 100%."""
    if not expected:
        return None
    present = set(tokens(hypothesis))
    hits = sum(1 for ident in expected if normalize(ident) in present)
    return hits / len(expected)


@dataclass
class ClipScore:
    clip_id: str
    category: str
    backend: str
    hinted: bool
    reference: str
    hypothesis: str
    wer: float
    identifier_recall: float | None
    latency_ms: int
    estimated_cost_usd: float | None
    audio_seconds: float | None


@dataclass
class Aggregate:
    backend: str
    hinted: bool
    category: str
    clips: int
    mean_wer: float
    # Mean over clips that have identifiers; None if none do.
    mean_identifier_recall: float | None
    p50_latency_ms: int
    p95_latency_ms: int
    total_cost_usd: float | None
    scores: list[ClipScore] = field(default_factory=list, repr=False)


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[k]


def aggregate(scores: list[ClipScore]) -> list[Aggregate]:
    """Groups by (backend, hinted, category) plus an "all" category row per
    (backend, hinted), so the report can show both the breakdown and the
    headline."""
    groups: dict[tuple[str, bool, str], list[ClipScore]] = {}
    for score in scores:
        groups.setdefault((score.backend, score.hinted, score.category), []).append(score)
        groups.setdefault((score.backend, score.hinted, "all"), []).append(score)

    out: list[Aggregate] = []
    for (backend, hinted, category), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] != "all", kv[0][2])):
        recalls = [s.identifier_recall for s in items if s.identifier_recall is not None]
        costs = [s.estimated_cost_usd for s in items if s.estimated_cost_usd is not None]
        latencies = [s.latency_ms for s in items]
        out.append(
            Aggregate(
                backend=backend,
                hinted=hinted,
                category=category,
                clips=len(items),
                mean_wer=sum(s.wer for s in items) / len(items),
                mean_identifier_recall=(sum(recalls) / len(recalls)) if recalls else None,
                p50_latency_ms=_percentile(latencies, 50),
                p95_latency_ms=_percentile(latencies, 95),
                total_cost_usd=round(sum(costs), 6) if costs else None,
                scores=items,
            )
        )
    return out
