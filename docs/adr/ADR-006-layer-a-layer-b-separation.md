# ADR-006: Hard separation between Layer A (structural/deterministic) and Layer B (semantic/LLM-inferred)

**Status:** Accepted

## Context

Generated study guides mix facts that are certain (an import exists, a function is defined at a line range) with facts that are inferred (why a design choice was likely made, what pattern a codebase follows). Conflating these erodes trust in the tool's output — a hallucinated architectural claim is far worse if it's indistinguishable from a parsed fact.

## Decision

Every fact in the system is tagged with its provenance:

- **Layer A** — deterministic, tree-sitter-extracted (imports, call graph, entry points). Ground truth. Lives in `backend/app/analysis/`.
- **Layer B** — LLM-inferred (module summaries, pattern detection, trade-off analysis), always grounded in Layer A facts and citations. Lives in `backend/app/semantics/`.

Diagram edges are Layer A; the LLM only adds labels/grouping on top (ADR-004). The two layers are kept in separate modules and, per the data model, effectively separate tables — never merged silently.

## Consequences

- When a diagram or claim looks wrong, it's immediately clear whether to debug the parser (Layer A) or the prompt (Layer B).
- Every Layer B claim in a study guide or quiz carries a `Citation` back to specific source lines (see §7 data model, ground rule #3 in `PROJECT_PLAN.md`).
- This separation is a design constraint on code structure, not just documentation — it must stay visible in the module/table layout.
