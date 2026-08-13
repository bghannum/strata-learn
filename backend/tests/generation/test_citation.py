from pathlib import Path

from app.generation.citation import MAX_SNIPPET_LINES, build_citation, read_snippet


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


def test_build_citation_truncates_snippets_beyond_max_lines(tmp_path: Path) -> None:
    # A whole-file citation on a large file must not persist the entire file
    # — bounds Citation storage/response size (see MAX_SNIPPET_LINES).
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "big.py").write_text("\n".join(f"line{i}" for i in range(1, 500)) + "\n")

    citation = build_citation(source_dir, "big.py", 1, 499, "claim")

    lines = citation.snippet_text.splitlines()
    assert len(lines) == MAX_SNIPPET_LINES + 1  # + the truncation marker
    assert lines[-1] == "… (truncated)"
    assert lines[0] == "line1"
    assert lines[MAX_SNIPPET_LINES - 1] == f"line{MAX_SNIPPET_LINES}"


def test_read_snippet_matches_build_citation_snippet_text(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("line1\nline2\nline3\n")

    assert read_snippet(source_dir, "app.py", 1, 2) == build_citation(source_dir, "app.py", 1, 2, "x").snippet_text
