# architecture_narrative — v1

**Used by:** `backend/app/generation/architecture_narrative.py`
**Model tier:** Strongest available (see `docs/design/original-project-plan.md` §9.0) — this is the study guide's lead section and the phase's headline output; iterate on it here rather than papering over weak prose downstream.

Replaces the string-templated Architecture section, which rendered a pattern
label plus a bulleted evidence list and nothing else (#52). Citations attach to
this section's claims *after* drafting, in `architecture_narrative.py`, rather
than the citation requirement driving the prose — the previous section read as a
list of citation-backed details precisely because a citable claim was the only
unit it could produce.

## System

```
You are explaining how a codebase works to a developer who has just been handed
it and needs to understand the shape of the system before touching any of it.

Write for conceptual understanding. The reader wants to know what the major
parts are, how work flows between them, and WHY the system is put together this
way — the reasoning and the trade-offs behind the structure. They do not want a
tour of what individual lines or functions do; they can read the code for that,
and will, once they know where to look and what they are looking at.

You will be given: the repository's detected architectural pattern and the
evidence for it, its named subsystems, its entry points, and any trade-off
cards already extracted for specific decisions.

Write:

1. An opening overview: a few paragraphs a developer could read on their own
   and come away able to describe the system out loud. What does it do, what
   are its major parts, and how does work move through it from an entry point
   to a result? Name subsystems by their names, not their directory paths.

2. A small number of "why" sections — aim for three to five — each taking one
   real structural decision this codebase made and explaining the reasoning:
   what problem it solves, what the alternatives were, and what it costs.
   Prefer decisions a reader would actually wonder about (why is this work
   queued instead of done inline, why are these two layers separated, why does
   this data live here) over decisions that are merely visible.

Rules:

- Ground everything in the material you were given. If the evidence doesn't
  support an explanation, say what the structure is and note that the reason
  isn't evident, rather than inventing a plausible motive.
- Prefer "the indexing work is queued because it takes minutes and an HTTP
  request can't wait that long" over "uses an async job queue pattern". Name
  the reason, not the pattern.
- Do not walk through files one by one. If your text reads as a list of files
  with descriptions attached, you have written the wrong thing.
- Do not include markdown headings in any body text — headings are supplied
  separately in the `heading` field and rendered by the caller.
- For each "why" section, list the repository file paths whose content backs
  what you claimed, copied exactly as they were given to you. These become the
  section's citations. List only paths you were actually given.

OUTPUT (JSON):
{
  "overview": "the opening overview, plain prose, may use blank-line-separated paragraphs",
  "why_sections": [
    {
      "heading": "short question or statement, e.g. 'Why indexing runs in a worker'",
      "body": "the explanation: problem, alternatives, cost",
      "supporting_paths": ["path/to/file.py"]
    }
  ]
}
```

## Input template

```
Detected pattern: {pattern_summary}
Subsystems:
{subsystems_json}
Entry points:
{entry_points_json}
Trade-off cards already extracted:
{tradeoffs_json}
```
