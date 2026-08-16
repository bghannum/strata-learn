import pytest

from app.audio.speakable import CODE_OMITTED, to_speakable


def test_strips_heading_markers_and_emphasis() -> None:
    result = to_speakable("## Architecture\n\nThe **worker** runs _asynchronously_.", max_chars=500)
    assert result.text == "Architecture The worker runs asynchronously."
    assert result.truncated is False


def test_drops_fenced_code_and_mermaid_blocks_with_a_spoken_marker() -> None:
    # A Mermaid fence read aloud is one character at a time; the learner has
    # the page for the diagram, so it's replaced rather than spoken.
    md = "Before.\n\n```mermaid\ngraph TD; A-->B;\n```\n\nAfter.\n\n```python\nprint('x')\n```"
    result = to_speakable(md, max_chars=500)
    assert result.text == f"Before. {CODE_OMITTED} After. {CODE_OMITTED}"
    assert "graph TD" not in result.text
    assert "print" not in result.text


def test_keeps_inline_code_contents_but_drops_the_backticks() -> None:
    # An identifier is still worth hearing — it's the punctuation around it
    # that isn't. Path separators inside are left alone.
    result = to_speakable("The job is `run_layer_b` in `app/worker/pipeline.py`.", max_chars=500)
    assert result.text == "The job is run_layer_b in app/worker/pipeline.py."


def test_unwraps_links_and_bullets() -> None:
    md = "- see [the ADR](docs/adr/ADR-002.md)\n- and *this*\n1. numbered"
    result = to_speakable(md, max_chars=500)
    assert result.text == "see the ADR and this numbered"


def test_truncates_at_a_sentence_boundary_and_says_so() -> None:
    md = "First sentence here. Second sentence here. Third sentence that goes past the cap."
    result = to_speakable(md, max_chars=45)
    assert result.text == "First sentence here. Second sentence here."
    assert result.truncated is True


def test_falls_back_to_a_word_boundary_when_one_sentence_overruns() -> None:
    result = to_speakable("word " * 20, max_chars=23)
    assert result.text == "word word word word"
    assert result.truncated is True


def test_short_text_is_not_marked_truncated() -> None:
    result = to_speakable("Short.", max_chars=100)
    assert result.text == "Short."
    assert result.truncated is False


def test_rejects_a_non_positive_cap() -> None:
    with pytest.raises(ValueError):
        to_speakable("x", max_chars=0)
