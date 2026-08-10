# fill_blank_generator — v1

**Used by:** `backend/app/quizzing/fill_blank_generator.py`
**Model tier:** Cheapest capable tier (see `PROJECT_PLAN.md` §9.0)

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
  "acceptable_alternatives": ["synonym or close-answer 1", "..."],
  "source_refs": [{"file_path": "...", "line_start": N, "line_end": N}]
}
```
