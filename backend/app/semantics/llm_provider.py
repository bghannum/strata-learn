"""LAYER B provider abstraction (ADR-003). Every semantic-analysis module calls
through `LLMProvider`, never the Anthropic SDK directly, so tests can inject
`FakeLLMProvider` instead of making real (billed, non-deterministic) API calls.

Only Anthropic is implemented (ADR-009 names both Anthropic and OpenAI, but
OPENAI_API_KEY is unset for now — an OpenAI backend would be untested against
a real key). One model for every Phase 2 task (PROJECT_PLAN.md D11).
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

import anthropic
from pydantic import BaseModel

MODEL_ID = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8192


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    parsed: BaseModel | None
    model: str
    stop_reason: str | None
    usage: dict[str, int]


class LLMProvider(Protocol):
    async def complete(
        self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None
    ) -> LLMResponse: ...


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        model: str = MODEL_ID,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("AnthropicProvider requires a non-empty api_key")
        self._client = client if client is not None else anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(
        self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        anthropic_messages = [{"role": m.role, "content": m.content} for m in messages]

        if response_schema is not None:
            # .parse()'s schema transform sets additionalProperties=false on every
            # object automatically — no need for the caller's model to declare
            # model_config = ConfigDict(extra="forbid") itself.
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=anthropic_messages,
                output_format=response_schema,
            )
            parsed = response.parsed_output
        else:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=anthropic_messages,
            )
            parsed = None

        text = next((block.text for block in response.content if block.type == "text"), "")
        return LLMResponse(
            text=text,
            parsed=parsed,
            model=response.model,
            stop_reason=response.stop_reason,
            usage={"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
        )


@dataclass
class RecordedCall:
    system: str
    messages: list[Message]
    response_schema: type[BaseModel] | None


class FakeLLMProvider:
    """Test double: returns canned responses in call order. No network access."""

    def __init__(self, responses: Iterable[LLMResponse]) -> None:
        self._responses: deque[LLMResponse] = deque(responses)
        self.calls: list[RecordedCall] = []

    async def complete(
        self, system: str, messages: list[Message], response_schema: type[BaseModel] | None = None
    ) -> LLMResponse:
        self.calls.append(RecordedCall(system=system, messages=messages, response_schema=response_schema))
        if not self._responses:
            raise AssertionError(
                f"FakeLLMProvider exhausted: {len(self.calls)} calls made but only "
                f"{len(self.calls) - 1} responses were seeded"
            )
        return self._responses.popleft()
