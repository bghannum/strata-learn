"""Domain errors for the voice layer. Narrow and catchable at the API
boundary, following fill_blank_grader.py's FillBlankLLMUnavailableError:
the route converts each to a specific HTTP status rather than letting a
provider SDK's own exception type leak up as a 500.

The 503 details the API produces for these are deliberately generic ("read-
aloud is temporarily unavailable"), never naming a backend — an error body
shouldn't disclose deployment config to a caller.
"""


class AudioValidationError(ValueError):
    """The upload failed a cheap structural check (size, container format)
    before any provider was involved. Same role as ingestion/source.py's
    SourcePreparationError, and converted to a 422 the same way."""


class AudioProviderError(RuntimeError):
    """A backend refused or failed the call. Wraps whatever the underlying
    SDK raised so callers catch one type regardless of backend."""


class TranscriptionUnavailableError(RuntimeError):
    """No transcription provider is configured for this deployment."""


class SpeechUnavailableError(RuntimeError):
    """No speech provider is configured for this deployment."""
