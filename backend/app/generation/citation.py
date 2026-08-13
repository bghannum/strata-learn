"""Reads real source snippets for study-guide citations while the cloned repo
is still on disk — pipeline.py deletes it right after generation finishes
(§8's temp dir lifecycle), so every Citation's snippet_text has to be
captured now, not fetched lazily later. Every study guide section builds its
Citation rows through this one helper, whatever Layer A/B evidence it's
citing.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CitationData:
    file_path: str
    line_start: int
    line_end: int
    claim_excerpt: str
    snippet_text: str


def build_citation(
    source_dir: Path, file_path: str, line_start: int, line_end: int, claim_excerpt: str
) -> CitationData:
    # errors="replace", matching parser.py/tradeoff_extractor.py's decoding
    # policy: Layer A parses on raw bytes and never raises on non-UTF-8
    # source, so citation capture can't fail just because Path.read_text()'s
    # strict-UTF-8 default would raise UnicodeDecodeError.
    text = (source_dir / file_path).read_bytes().decode("utf-8", errors="replace")
    lines = text.splitlines()
    snippet = "\n".join(lines[line_start - 1 : line_end])
    return CitationData(
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        claim_excerpt=claim_excerpt,
        snippet_text=snippet,
    )
