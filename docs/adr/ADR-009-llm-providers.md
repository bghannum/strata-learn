# ADR-009: LLM providers — Anthropic + OpenAI only, no self-hosted/open-source models (for now)

**Status:** Accepted (revisit as Phase 7+ stretch)

## Context

Open-source models (self-hosted via Ollama/vLLM, or hosted via Together/Groq/Fireworks) were considered. Introducing them during initial prompt iteration would add a second unknown variable — model quality vs. prompt quality — at exactly the point where prompts (especially the trade-off extractor, §9.3) are least stable.

## Decision

Use only Anthropic and OpenAI, both behind the ADR-003 provider abstraction, with per-task model selection per §9.0. Self-hosted/OSS inference serving is a legitimate but separate scope of work (infra/ops, not product) and is explicitly deferred.

## Consequences

- Two provider backends to implement in `semantics/llm_provider.py`, not more, for v1.
- API keys for both providers are required local/hosted configuration (see root `README.md` prerequisites).
- Revisit as a deliberate Phase 7+ stretch project once the pipeline and prompts are stable — at that point OSS models can be A/B'd against the same fixed prompts for a clean comparison.
