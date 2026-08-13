"""Reads real source snippets for study-guide citations while the cloned repo
is still on disk — pipeline.py deletes it right after generation finishes
(§8's temp dir lifecycle), so every Citation's snippet_text has to be
captured now, not fetched lazily later. Every study guide section builds its
Citation rows through this one helper, whatever Layer A/B evidence it's
citing.
"""

from dataclasses import dataclass
from pathlib import Path

# A citation's snippet is meant to substantiate a claim, not embed a whole
# file — but deep-dive/glossary citations reuse a module's whole-file line
# range (study_guide_builder.py's _whole_file_citation), and the walker
# allows files up to 1 MiB. Without a cap, a module with many key_concepts
# could persist that same large snippet once per concept, ballooning both
# Citation storage and the /study-guides/{id} response (found via Codex's
# Phase 3 pre-push review).
MAX_SNIPPET_LINES = 200


@dataclass(frozen=True)
class CitationData:
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str
    snippet_text: str


def read_snippet(source_dir: Path, file_path: str, line_start: int, line_end: int) -> str:
    # errors="replace", matching parser.py/tradeoff_extractor.py's decoding
    # policy: Layer A parses on raw bytes and never raises on non-UTF-8
    # source, so citation capture can't fail just because Path.read_text()'s
    # strict-UTF-8 default would raise UnicodeDecodeError.
    text = (source_dir / file_path).read_bytes().decode("utf-8", errors="replace")
    lines = text.splitlines()[line_start - 1 : line_end]
    truncated = len(lines) > MAX_SNIPPET_LINES
    if truncated:
        lines = lines[:MAX_SNIPPET_LINES]
    snippet = "\n".join(lines)
    if truncated:
        snippet += "\n… (truncated)"
    return snippet


def build_citation(
    source_dir: Path, file_path: str, line_start: int, line_end: int, claim_excerpt: str
) -> CitationData:
    return CitationData(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        claim_excerpt=claim_excerpt,
        snippet_text=read_snippet(source_dir, file_path, line_start, line_end),
    )
