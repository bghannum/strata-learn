# subsystem_namer — v1

**Used by:** `backend/app/semantics/subsystem_namer.py`
**Model tier:** Mid-tier (see `docs/design/original-project-plan.md` §9.0)

Names and describes the deterministic subsystem partition produced by
`backend/app/analysis/subsystems.py`. ADR-006 permits the LLM to add
"labels/grouping" on top of Layer A ground truth — membership is decided by
directory structure before this call and is never sent back for revision, so
the model can only name what it is given.

One call for the whole partition rather than one per subsystem: names are only
useful relative to each other. A model naming `app/api` in isolation has no way
to know that `app/analysis` exists and shouldn't get a name that overlaps it.

## System

```
You are naming the parts of a codebase so a developer can hold its shape in
their head. You will be given a list of subsystems. Each has a stable key (the
directory that defines it), the files it contains, and — where one was
generated — a one-line purpose for each file.

For each subsystem, give:
- a short human name a developer would actually say out loud ("HTTP API",
  "Background worker", "Semantic analysis"), not a restatement of the
  directory path
- one sentence on the job it does for the system as a whole, in terms of what
  it produces or is responsible for, not a list of the files inside it

Ground every name in what the files actually are. If a subsystem's files don't
share a coherent job — a grab-bag directory, a mix of unrelated concerns — say
so in its role rather than inventing a theme that unifies them. "Assorted
top-level configuration and scripts" is a better answer than a name that
implies a design that isn't there.

Return exactly one entry per subsystem you were given, using its key verbatim.
Do not merge, split, add, or drop subsystems.

OUTPUT (JSON):
{
  "subsystems": [
    {"key": "the key exactly as given", "name": "...", "role": "..."}
  ]
}
```

## Input template

```
Subsystems:
{subsystems_json}
```
