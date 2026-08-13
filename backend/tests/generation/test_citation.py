from pathlib import Path

from app.generation.citation import build_citation


def test_build_citation_reads_the_requested_line_range(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("line1\nline2\nline3\nline4\n")

    citation = build_citation(source_dir, "app.py", 2, 3, "explains lines 2-3")

    assert citation.file_path == "app.py"
    assert citation.line_start == 2
    assert citation.line_end == 3
    assert citation.claim_excerpt == "explains lines 2-3"
    assert citation.snippet_text == "line2\nline3"


def test_build_citation_tolerates_non_utf8_bytes(tmp_path: Path) -> None:
    # Matches parser.py/tradeoff_extractor.py's decoding policy: Layer A never
    # raises on non-UTF-8 source, so citation capture can't fail here either.
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "app.py").write_bytes(b"line1\n\xff\xfe not valid utf-8\nline3\n")

    citation = build_citation(source_dir, "app.py", 1, 2, "claim")

    assert "line1" in citation.snippet_text
