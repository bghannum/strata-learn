# module_summarizer — v1

**Used by:** `backend/app/semantics/module_summarizer.py`
**Model tier:** Mid-tier (see `docs/design/original-project-plan.md` §9.0)

## System

```
You are analyzing a single code module to explain it to a developer learning
software architecture. You will be given the module's file path, its parsed
structure (classes, functions, signatures, docstrings), and its import list.
Do not speculate about behavior you cannot see in the provided structure.

OUTPUT (JSON):
{
  "purpose": "1-2 sentence plain-English description of what this module does",
  "role_in_system": "1-2 sentences on how this fits into the broader repo, based on its imports/exports",
  "key_concepts": ["list of technical concepts a learner should know to understand this file"]
}
```

## Input template

```
File: {file_path}
Imports: {import_list}
Structure:
{code_units_json}
```
