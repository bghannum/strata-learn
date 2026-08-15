# fill_blank_generator — v2

**Used by:** `backend/app/quizzing/fill_blank_generator.py`
**Model tier:** Cheapest capable tier (see `docs/design/original-project-plan.md` §9.0)

## Changes from v1

v1 offered CODE and CONCEPT modes and said only "prefer CONCEPT mode for
architecture/pattern testing", which left CODE mode as the easy default for any
seed that came with a snippet — and every seed comes with a snippet. v2 makes
CONCEPT the default and requires CODE mode to earn its use: the blanked token has
to be one whose value is a design decision, not merely a token that happens to
be in the code.

The same shift is applied to `mcq_generator.v2.md`.

## System

```
Generate a fill-in-the-blank question that tests whether a developer UNDERSTANDS
this part of the codebase, rather than whether they can recall its text.

Two modes:
- CONCEPT mode (default): blank a key term in a sentence about how or why the
  system works — "Indexing runs in a ___ so an HTTP request doesn't have to
  wait for it to finish". The blanked term should be one a developer can only
  fill in correctly if they understand the design.
- CODE mode: blank a meaningful token from a real code snippet. Use this ONLY
  when the token's value is itself the decision worth knowing — a queue name
  two subsystems have to agree on, a timeout that encodes a real constraint. Do
  not blank a token merely because it appears in the snippet: a blanked local
  variable or an arbitrary function name tests transcription, not
  understanding.

If you cannot find a token that meets the CODE-mode bar, write a CONCEPT
question instead. CONCEPT is always an acceptable answer; CODE is not.

Blank exactly one term, and make sure the surrounding text gives enough context
that exactly one answer is defensible — a sentence with several equally valid
fillings is a broken question, not a hard one.

List acceptable alternatives generously: synonyms, the singular/plural form, and
the common short name for the same thing. A learner who understands the concept
should not be marked wrong for naming it slightly differently.

OUTPUT (JSON):
{
  "mode": "code | concept",
  "blanked_text": "text with ___ marking the blank",
  "correct_answer": "the answer",
  "acceptable_alternatives": ["synonym or close-answer 1", "..."]
}
```

## Input template

```
Concept/fact to test: {source_fact}
Source code context: {code_snippet}
```

## Note

No `source_refs` in the output, unlike the original plan sketch (`docs/design/original-project-plan.md` §9.5). Each question is generated from exactly one already-persisted `Citation` (see `backend/app/quizzing/generation.py`) — that citation's own `file_path`/`line_start`/`line_end` is the grounding, attached deterministically by the caller, not self-reported by the model. There's nothing to validate a free-form LLM citation against here (no `CodeUnit` line-span check like `tradeoff_extractor.py` has), so asking for one would just add an untrusted field. Same reasoning applies to `mcq_generator.v2.md`.
