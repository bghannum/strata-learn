# mcq_generator — v1

**Used by:** `backend/app/quizzing/mcq_generator.py`
**Model tier:** Cheapest capable tier (see `docs/design/original-project-plan.md` §9.0) — high-volume generation, optimize for cost.

## System

```
Generate a multiple-choice question testing understanding of the following
code/concept. Distractors (wrong answers) must be PLAUSIBLE — pull them from
real alternative approaches, common misconceptions, or adjacent concepts in
the same codebase. Do not use obviously-wrong joke answers.

OUTPUT (JSON):
{
  "prompt": "the question text",
  "choices": ["choice A", "choice B", "choice C", "choice D"],
  "correct_index": 0,
  "explanation": "why the correct answer is correct and others are not",
  "source_refs": [{"file_path": "...", "line_start": N, "line_end": N}]
}
```

## Input template

```
Concept/fact to test: {source_fact}
Source code context: {code_snippet}
```
