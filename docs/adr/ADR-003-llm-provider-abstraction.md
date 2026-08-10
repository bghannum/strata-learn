# ADR-003: LLM provider abstraction

**Status:** Accepted

## Context

Layer B (semantic analysis, generation, grading fallback) calls LLMs for several distinct tasks with different reasoning demands (see `PROJECT_PLAN.md` §9.0). Calling vendor SDKs directly from feature code would couple every call site to a specific provider and make per-task model selection and prompt/model A/B testing harder.

## Decision

Define an internal interface in `backend/app/semantics/llm_provider.py`:

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        system: str,
        messages: list[Message],
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse: ...
```

All generation and semantic-analysis code calls this interface, never a vendor SDK directly.

## Consequences

- Model selection becomes a config/allocation concern (§9.0), not a code-level one.
- Anthropic and OpenAI backends (ADR-009) can be swapped per-task without touching call sites.
- Prompt quality and model quality can be varied independently when iterating.
