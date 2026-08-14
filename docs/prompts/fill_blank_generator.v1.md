# fill_blank_generator — v1

**Used by:** `backend/app/quizzing/fill_blank_generator.py`
**Model tier:** Cheapest capable tier (see `docs/design/original-project-plan.md` §9.0)

## System

```
Generate a fill-in-the-blank question. Two modes:
- CODE mode: blank out a meaningful token from a real code snippet (a
  function name, a config value, a key parameter) — not trivial syntax.
- CONCEPT mode: blank out a key term in a conceptual sentence about the
  architecture (e.g., "This service uses ___ to decouple producers from
  consumers").
Prefer CONCEPT mode for architecture/pattern testing, CODE mode for testing
recall of actual implementation details.

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

No `source_refs` in the output, unlike the original plan sketch (`docs/design/original-project-plan.md` §9.5). Each question is generated from exactly one already-persisted `Citation` (see `backend/app/quizzing/generation.py`) — that citation's own `file_path`/`line_start`/`line_end` is the grounding, attached deterministically by the caller, not self-reported by the model. There's nothing to validate a free-form LLM citation against here (no `CodeUnit` line-span check like `tradeoff_extractor.py` has), so asking for one would just add an untrusted field. Same reasoning applies to `mcq_generator.v1.md`.
