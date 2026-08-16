"""Turns a persisted Markdown section into text worth hearing.

Section.content_md fed raw to a TTS model comes back as "hash hash
Architecture … backtick app slash worker slash pipeline dot py backtick",
and a Mermaid fence gets spelled out one character at a time. So this
strips the syntax that only means something visually, drops code and
diagram blocks entirely (spoken code is noise; the learner has the page for
that), and truncates on a sentence boundary under the caller's cap — which
is how the provider's hard input limit is enforced *before* a paid call
rather than by eating a 400.

Pure and deterministic: no provider, no I/O. That makes it the best unit-
test target in the voice layer, and it's why the route calls it rather than
trusting the provider to cope.
"""

import re
from dataclasses import dataclass

CODE_OMITTED = "Code example omitted."

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_STAR_EMPHASIS = re.compile(r"(\*\*|\*)(?=\S)(.+?)(?<=\S)\1")
# Underscore emphasis only at word edges — Markdown's own rule, and the one
# that keeps `run_layer_b` from being read as "run" + emphasised "layer" +
# "b". An identifier's underscores are content, not markup.
_UNDERSCORE_EMPHASIS = re.compile(r"(?<![A-Za-z0-9])(__|_)(?=\S)(.+?)(?<=\S)\1(?![A-Za-z0-9])")
_HRULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{2,}")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


@dataclass(frozen=True)
class SpeakableText:
    text: str
    # True when the cap cut something off — the UI says "reading the first
    # part of this section" rather than letting the audio just stop.
    truncated: bool


def _strip_markdown(markdown: str) -> str:
    text = _FENCE.sub(f" {CODE_OMITTED} ", markdown)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    text = _HRULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    # Backticks go, contents stay: an identifier is still worth hearing, it's
    # the punctuation around it that isn't. Path separators and underscores
    # inside are left alone — the model handles "app/worker/pipeline.py"
    # better than any respelling here would.
    text = _INLINE_CODE.sub(r"\1", text)
    # Each applied twice so nested bold-italic unwraps fully.
    for pattern in (_STAR_EMPHASIS, _UNDERSCORE_EMPHASIS, _STAR_EMPHASIS, _UNDERSCORE_EMPHASIS):
        text = pattern.sub(r"\2", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n", text)
    # Paragraph breaks become a pause the model can hear; single newlines
    # inside a paragraph are just wrapping.
    return " ".join(line.strip() for line in text.split("\n") if line.strip()).strip()


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Cuts at the last sentence end under the cap. Falls back to the last
    word boundary when a single sentence overruns it — a mid-word cut is
    the one thing worse than a mid-sentence one."""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    last_end = None
    for match in _SENTENCE_END.finditer(window):
        last_end = match.end()
    if last_end is not None and last_end > 0:
        return window[:last_end].rstrip()
    last_space = window.rfind(" ")
    if last_space > 0:
        return window[:last_space].rstrip()
    return window.rstrip()


def to_speakable(markdown: str, *, max_chars: int) -> SpeakableText:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    plain = _strip_markdown(markdown)
    spoken = _truncate_at_sentence(plain, max_chars)
    return SpeakableText(text=spoken, truncated=len(spoken) < len(plain))
