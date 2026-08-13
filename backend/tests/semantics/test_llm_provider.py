"""No real Anthropic API calls anywhere in this file — AnthropicProvider is
tested via an injected stub client (fake .messages.create/.parse), never a
constructed anthropic.AsyncAnthropic hitting the network."""

from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from app.semantics.llm_provider import (
    AnthropicProvider,
    FakeLLMProvider,
    LLMResponse,
    Message,
)


@dataclass
class _StubTextBlock:
    text: str
    type: str = "text"


@dataclass
class _StubUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _StubMessage:
    content: list
    model: str
    stop_reason: str | None
    usage: _StubUsage
    parsed_output: BaseModel | None = None


class _StubMessagesResource:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.parse_calls: list[dict] = []
        self.create_response: _StubMessage | None = None
        self.parse_response: _StubMessage | None = None

    async def create(self, **kwargs) -> _StubMessage:
        self.create_calls.append(kwargs)
        assert self.create_response is not None
        return self.create_response

    async def parse(self, **kwargs) -> _StubMessage:
        self.parse_calls.append(kwargs)
        assert self.parse_response is not None
        return self.parse_response


@dataclass
class _StubClient:
    messages: _StubMessagesResource = field(default_factory=_StubMessagesResource)


class _ExtractedOutput(BaseModel):
    value: str


async def test_anthropic_provider_complete_without_schema() -> None:
    client = _StubClient()
    client.messages.create_response = _StubMessage(
        content=[_StubTextBlock(text="hello there")],
        model="claude-sonnet-5",
        stop_reason="end_turn",
        usage=_StubUsage(input_tokens=10, output_tokens=5),
    )

    provider = AnthropicProvider(api_key="test-key", client=client)
    result = await provider.complete(system="be helpful", messages=[Message(role="user", content="hi")])

    assert result.text == "hello there"
    assert result.parsed is None
    assert result.model == "claude-sonnet-5"
    assert result.stop_reason == "end_turn"
    assert result.usage == {"input_tokens": 10, "output_tokens": 5}

    assert len(client.messages.create_calls) == 1
    call = client.messages.create_calls[0]
    assert call["system"] == "be helpful"
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert not client.messages.parse_calls


async def test_anthropic_provider_complete_with_schema() -> None:
    client = _StubClient()
    parsed = _ExtractedOutput(value="x")
    client.messages.parse_response = _StubMessage(
        content=[_StubTextBlock(text='{"value": "x"}')],
        model="claude-sonnet-5",
        stop_reason="end_turn",
        usage=_StubUsage(input_tokens=20, output_tokens=8),
        parsed_output=parsed,
    )

    provider = AnthropicProvider(api_key="test-key", client=client)
    result = await provider.complete(
        system="extract", messages=[Message(role="user", content="extract this")], response_schema=_ExtractedOutput
    )

    assert result.parsed == parsed
    assert result.text == '{"value": "x"}'

    assert len(client.messages.parse_calls) == 1
    assert client.messages.parse_calls[0]["output_format"] is _ExtractedOutput
    assert not client.messages.create_calls


def test_anthropic_provider_empty_api_key_raises() -> None:
    with pytest.raises(ValueError, match="non-empty api_key"):
        AnthropicProvider(api_key="")


def _response(marker: str) -> LLMResponse:
    return LLMResponse(text=marker, parsed=None, model="fake", stop_reason="end_turn", usage={})


async def test_fake_llm_provider_returns_in_order() -> None:
    provider = FakeLLMProvider([_response("first"), _response("second")])

    first = await provider.complete(system="s", messages=[Message(role="user", content="a")])
    second = await provider.complete(system="s", messages=[Message(role="user", content="b")])

    assert first.text == "first"
    assert second.text == "second"


async def test_fake_llm_provider_records_calls() -> None:
    provider = FakeLLMProvider([_response("only")])
    await provider.complete(system="sys prompt", messages=[Message(role="user", content="input")])

    assert len(provider.calls) == 1
    assert provider.calls[0].system == "sys prompt"
    assert provider.calls[0].messages == [Message(role="user", content="input")]


async def test_fake_llm_provider_raises_clearly_on_exhaustion() -> None:
    provider = FakeLLMProvider([])
    with pytest.raises(AssertionError, match="exhausted"):
        await provider.complete(system="s", messages=[Message(role="user", content="a")])
