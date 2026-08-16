# short_answer_generator — v1

**Used by:** `backend/app/quizzing/short_answer_generator.py`
**Model tier:** Cheapest capable tier (see `docs/design/original-project-plan.md` §9.0)

## Why this exists

Fill-in-the-blank (`fill_blank_generator.v2.md`) was the second question type
through Phase 8. Even at its best — v2 made CONCEPT mode the default and made
CODE mode earn its use — a blanked term still asks the learner to guess the one
word the generator was thinking of, and exact-match-first grading rewards
recall over understanding. It read as a gotcha quiz. This replaces it in new
quizzes with the counterpart to Phase 6's architecture narrative: an open
how/why question, answered in a sentence or three, graded against a rubric of
key points rather than a string.

## System

```
Write ONE short-answer question that tests whether a developer UNDERSTANDS this
part of the codebase — why it is built this way, what role it plays in the
larger system, what problem it solves, or what would break if it were done
differently. The learner will answer in one to three sentences, in their own
words, possibly by speaking.

A good question is one a developer could answer after genuinely understanding
this part of the system, and could not answer by pattern-matching against the
snippet's text. Ask about reasoning and consequences, not names or values:
"Why does indexing run in a background job rather than in the HTTP request?"
is good; "What is the function on line 12 called?" is not. Prefer questions
whose answer connects this piece to the rest of the system.

Also write:

- A MODEL ANSWER: two to four sentences a strong developer might give. Plain
  prose, no bullet points. It is shown to the learner *after* grading, so it
  should teach — say why, not just what.

- A RUBRIC of 2 to 4 KEY POINTS: the distinct ideas a complete answer must
  contain. Each point is one short sentence stating an idea, not a keyword —
  "an HTTP request would time out waiting for a multi-minute clone and parse",
  not "timeout". A grader will judge each point separately and the score is
  the fraction addressed, so the points must be genuinely distinct and each
  one must be worth credit on its own. Do not include a point that merely
  restates the question.

OUTPUT (JSON):
{
  "prompt": "the question",
  "model_answer": "two to four sentences",
  "rubric": ["key point 1", "key point 2", "..."]
}
```

## Input template

```
Concept/fact to test: {source_fact}
Source code context: {code_snippet}
```

## Note

Same grounding contract as `mcq_generator.v2.md`: each question is generated
from exactly one already-persisted `Citation`, whose `file_path`/`line_start`/
`line_end` the caller attaches deterministically. No self-reported citation
in the output.
