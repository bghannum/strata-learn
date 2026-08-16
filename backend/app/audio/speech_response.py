"""The one way a speech route turns text into an HTTP response, shared by
the study-guide section route and the quiz-feedback route so the two can't
drift on the part that's easy to get wrong.

## Why the first chunk is pulled *before* StreamingResponse is returned

Once a StreamingResponse starts, the status line is already on the wire. A
provider that refuses the request after that — bad key, rate limit, model
outage — can only truncate a 200, which the frontend sees as a corrupt
audio file with no message. Awaiting the first chunk here, inside the
handler, converts every "the provider refused" failure into a real 503
with a `detail` body the UI can render. Failures *mid-stream* remain
unrepresentable as a status; that's accepted and logged (metering records
the call as not-ok).

Cache-Control: no-store because generated speech is never persisted
(ADR-010), is per-user, and is paid for. No Content-Length — unknown by
construction.
"""

import logging
from collections.abc import AsyncIterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.audio.errors import AudioProviderError
from app.audio.providers import SpeechProvider
from app.audio.speakable import to_speakable

logger = logging.getLogger("strata.voice")

# Surfaced as a response header so the UI can say "reading the first part of
# this section" when the text was cut at the provider's input limit, without
# needing a second request or a JSON envelope around an audio body.
TRUNCATED_HEADER = "X-Speech-Truncated"


async def stream_speech(provider: SpeechProvider | None, markdown: str, *, max_chars: int) -> StreamingResponse:
    if provider is None:
        # Generic on purpose: which backend (or none) is operator config, not
        # something a caller learns from an error body.
        raise HTTPException(503, "read-aloud is unavailable on this deployment")

    speakable = to_speakable(markdown, max_chars=max_chars)
    if not speakable.text:
        raise HTTPException(422, "there is nothing to read aloud in this section")

    agen = provider.synthesize(speakable.text)
    try:
        first = await anext(agen)
    except StopAsyncIteration as exc:
        raise HTTPException(502, "the speech provider returned no audio") from exc
    except AudioProviderError as exc:
        raise HTTPException(503, "read-aloud is temporarily unavailable — please try again") from exc

    async def body() -> AsyncIterator[bytes]:
        yield first
        try:
            async for chunk in agen:
                yield chunk
        except AudioProviderError:
            # Too late for a status code; the client gets a truncated body.
            # Metering (inside the provider) has already recorded ok=False.
            logger.warning("voice.speech stream failed after the first chunk")

    return StreamingResponse(
        body(),
        media_type=provider.media_type,
        headers={"Cache-Control": "no-store", TRUNCATED_HEADER: "1" if speakable.truncated else "0"},
    )
