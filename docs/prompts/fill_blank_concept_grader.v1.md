# fill_blank_concept_grader — v1

**Used by:** `backend/app/quizzing/grading/fill_blank_grader.py`
**Model tier:** N/A — fallback only, after exact/alternative match fails. See `docs/design/original-project-plan.md` §10.2.

## System

```
Grade this fill-in-the-blank answer. Judge whether the student's answer is
conceptually equivalent to the correct answer, even if worded differently.
Do not require exact wording. Award partial credit (0.5) if the answer is
directionally correct but imprecise, and 0.0 if wrong.

OUTPUT (JSON): {"score": 0.0 | 0.5 | 1.0, "feedback": "1-2 sentences"}
```

## Input template

```
Correct answer: {correct_answer}
Acceptable alternatives: {alternatives}
Student answer: {student_answer}
```

## Note

Concept-mode grading tries an exact/alternative-list match first (`§10.2`); this LLM-judge prompt only runs as a fallback when that fails. Code-mode answers never reach this prompt — they are graded by exact match only.

Correct-answer/alternatives moved from `## System` to `## Input template` in an editing pass before this prompt's first real use: `PromptTemplate.system` is passed to the LLM provider as-is (see `app/semantics/prompts.py`), never through `.format()` — only `render_input()` (built from the `## Input template` block) substitutes `{placeholders}`. The original draft would have sent the literal, unsubstituted string `"{correct_answer}"` to the model on every call.
