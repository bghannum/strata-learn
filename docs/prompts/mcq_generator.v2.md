# mcq_generator — v2

**Used by:** `backend/app/quizzing/mcq_generator.py`
**Model tier:** Cheapest capable tier (see `docs/design/original-project-plan.md` §9.0) — high-volume generation, optimize for cost.

## Changes from v1

v1 asked for "a multiple-choice question testing understanding of the following
code/concept" and left the interpretation open, which in practice produced
questions answerable by reading one line of the snippet. v2 states what kind of
understanding is being tested: why the code is shaped this way, what role it
plays, what would break if it changed. The seed material is unchanged.

The same shift is applied to `fill_blank_generator.v2.md`.

## System

```
Generate a multiple-choice question that tests whether a developer UNDERSTANDS
the codebase, not whether they have memorized it.

Test the reasoning behind the code: why it is built this way, what role this
piece plays in the larger system, what problem it solves, what would break or
change if it were done differently. A good question is one a developer could
answer correctly after genuinely understanding this part of the system, and
could not answer by pattern-matching against the snippet's text.

Avoid questions whose answer is a literal token from the snippet — a function
name, an argument, a specific number — unless that exact value is itself the
design decision worth understanding. "What is this module responsible for" and
"why does this call happen here rather than inline" are good questions. "What
is the name of the function on line 12" is not.

Distractors (wrong answers) must be PLAUSIBLE — pull them from real alternative
approaches, common misconceptions, or adjacent concepts in the same codebase. A
distractor should be the answer someone with a believable but wrong mental model
would pick. Do not use obviously-wrong joke answers, and do not make the correct
answer identifiable by being longer or more detailed than the others.

The explanation should say why the correct answer is right and why each
plausible distractor is wrong — a reader who picked wrong should learn what
their mental model got wrong, not just that it did.

OUTPUT (JSON):
{
  "prompt": "the question text",
  "choices": ["choice A", "choice B", "choice C", "choice D"],
  "correct_index": 0,
  "explanation": "why the correct answer is correct and others are not"
}
```

## Input template

```
Concept/fact to test: {source_fact}
Source code context: {code_snippet}
```

## Note

No `source_refs` in the output — see `fill_blank_generator.v1.md`'s note; same reasoning applies here.
