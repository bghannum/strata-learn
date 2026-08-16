"""Vocabulary hints for transcription: the technical terms a spoken quiz
answer is likely to contain, drawn from the code the question was generated
from, so the ASR model has a fighting chance at "asyncpg" and "run_layer_b"
instead of "a sink PG" and "run layer bee".

## The one rule that matters here: the answer key is never a hint

An ASR prompt *biases decoding toward the supplied terms* — that's the whole
mechanism. For a code-mode fill-in-the-blank, graded by exact match in
quizzing/grading/fill_blank_grader.py, priming the model with the correct
token means a mumbled approximation can decode *as* the answer. That turns
an accessibility feature into an answer oracle, and it's invisible in the
transcript the learner sees — it just looks like excellent transcription.

So Question.correct_answer and acceptable_alternatives are excluded by
construction, and any term that reaches this from another source but
happens to match them is subtracted too. Citation.snippet_text is the right
source precisely because it's the code the question was *drawn from* rather
than the answer *to* it — the identifiers surrounding a blank, not the
blank. test_vocabulary.py has a dedicated test for this; keep it.

Bounded twice — by term count and by joined character length — because
the result is interpolated into a paid request.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence

# Identifiers, dotted names, and slashed paths, as they appear in source.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z0-9_]+)*")
# Every separator an identifier or path can be broken on, for the answer-key
# component check below.
_COMPONENTS = re.compile(r"[/._\-]")

# Extensions that mark a bare word as a filename worth keeping ("main.py"),
# where a plain word ("main") would be dropped as ordinary English.
_CODE_EXTENSIONS = frozenset({"py", "js", "ts", "tsx", "jsx", "json", "yml", "yaml", "toml", "md", "sql", "sh"})

# Not worth priming for — the model already knows these, and they crowd out
# the terms it doesn't.
_STOPWORDS = frozenset(
    {
        "self", "cls", "def", "class", "return", "import", "from", "async", "await", "if", "else", "elif",
        "for", "while", "in", "not", "and", "or", "is", "none", "true", "false", "with", "as", "try",
        "except", "finally", "raise", "pass", "lambda", "yield", "the", "of", "to", "a", "an",
        "const", "let", "var", "function", "export", "default", "new", "this", "null", "undefined",
    }
)


def _looks_technical(term: str) -> bool:
    """A term earns a hint slot when its shape says "identifier", not
    "English": an underscore, an internal capital, a path separator, or a
    code-file extension. Plain lowercase words are left to the model."""
    if "_" in term or "/" in term:
        return True
    if any(ch.isupper() for ch in term[1:]):
        return True
    if "." in term:
        return term.rsplit(".", 1)[-1].lower() in _CODE_EXTENSIONS
    return False


def _distinct_path_segments(file_path: str) -> list[str]:
    """The basename plus any directory segment that reads as a name rather
    than as scaffolding: "app/quizzing/mastery.py" -> ["mastery.py",
    "quizzing"]. Generic segments like "app" or "src" carry no signal."""
    parts = [p for p in file_path.replace("\\", "/").split("/") if p]
    if not parts:
        return []
    out = [parts[-1]]
    for segment in parts[:-1]:
        if segment.lower() not in {"app", "src", "lib", "backend", "frontend", "tests", "test", "pkg"}:
            out.append(segment)
    return out


def _normalize(term: str) -> str:
    return term.strip().lower()


def build_vocabulary(
    *,
    snippet_text: str | None,
    file_path: str | None,
    subsystem_name: str | None,
    excluded_terms: Iterable[str],
    max_terms: int,
    max_chars: int,
) -> list[str]:
    """Selects and orders the hint terms. Callers pass the question's answer
    key as `excluded_terms` — see the module docstring for why that's the
    exclusion list rather than a source."""
    excluded = {_normalize(t) for t in excluded_terms if t and t.strip()}

    counts: Counter[str] = Counter()
    display: dict[str, str] = {}

    def add(term: str, weight: int = 1) -> None:
        key = _normalize(term)
        if not key or key in _STOPWORDS or key in excluded:
            return
        # The exclusion also has to catch terms the answer is a *component*
        # of: with "snapshot" as the answer, priming "snapshot.py" or
        # "create_snapshot" still primes the answer. Over-excluding costs a
        # hint; under-excluding leaks the key — so this errs strict and
        # splits on every separator an identifier can contain.
        if excluded and any(part in excluded for part in _COMPONENTS.split(key) if part):
            return
        counts[key] += weight
        display.setdefault(key, term)

    for term in _TOKEN.findall(snippet_text or ""):
        if _looks_technical(term):
            add(term)

    if file_path:
        for segment in _distinct_path_segments(file_path):
            add(segment, weight=2)

    if subsystem_name and subsystem_name.strip():
        add(subsystem_name.strip(), weight=2)

    ranked = [display[key] for key, _count in counts.most_common()]
    ranked = ranked[:max_terms]

    # The character cap wins over the term cap: it's the one that bounds the
    # paid request.
    chosen: list[str] = []
    length = 0
    for term in ranked:
        extra = len(term) + (2 if chosen else 0)  # ", " separator
        if length + extra > max_chars:
            break
        chosen.append(term)
        length += extra
    return chosen


def technical_terms(text: str) -> list[str]:
    """The identifier-shaped tokens in a piece of prose — for callers that
    need to *exclude* what a rubric or answer key names, rather than build
    hints from it. Same shape test as the hint selection above, so the two
    agree on what counts."""
    return [term for term in _TOKEN.findall(text or "") if _looks_technical(term)]


def build_vocabulary_prompt(terms: Sequence[str]) -> str:
    """The one string both backends condition on. Whisper-family models
    treat the prompt as preceding context, so a short lead-in and a comma
    list reads naturally to them; keeping the format here (not per-backend)
    is what lets an evaluation attribute differences to the model."""
    if not terms:
        return ""
    return "Technical terms: " + ", ".join(terms) + "."
