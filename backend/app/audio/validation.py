"""Cheap structural validation of a microphone upload, before any provider
sees it. The direct analogue of ingestion/source.py's validate_zip_upload,
which reads only the zip central directory: sniff the container from its
first bytes, don't decode anything.

Sniffing rather than trusting the declared Content-Type, because the
declared type is whatever the client chose to send. The allowlist is the
set MediaRecorder actually produces across browsers (webm/Opus on Chrome
and Firefox, mp4/AAC on Safari) plus the two the evaluation fixtures use
(WAV, Ogg). Both provider backends accept all four without transcoding —
OpenAI natively, faster-whisper via PyAV's bundled FFmpeg — so nothing
here needs an ffmpeg dependency.
"""

from dataclasses import dataclass

from app.audio.errors import AudioValidationError


@dataclass(frozen=True)
class AudioFormat:
    content_type: str
    extension: str


_WEBM = AudioFormat(content_type="audio/webm", extension="webm")
_MP4 = AudioFormat(content_type="audio/mp4", extension="mp4")
_WAV = AudioFormat(content_type="audio/wav", extension="wav")
_OGG = AudioFormat(content_type="audio/ogg", extension="ogg")

# Enough of the file to identify every allowlisted container: the longest
# signature checked below ends at byte 12.
SNIFF_BYTES = 12


def sniff_audio_format(data: bytes) -> AudioFormat | None:
    """Identifies the container from its leading bytes, or None if it's not
    one this endpoint accepts."""
    head = data[:SNIFF_BYTES]
    # EBML header — Matroska/WebM.
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return _WEBM
    # ISO BMFF: a 4-byte box size then "ftyp". Covers mp4, m4a, and Safari's
    # fragmented output. Deliberately not checking the brand after "ftyp" —
    # Safari's MediaRecorder emits several, and the box name is what
    # distinguishes an ISO container from anything else here.
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return _MP4
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return _WAV
    if head.startswith(b"OggS"):
        return _OGG
    return None


def validate_audio_upload(data: bytes, max_bytes: int) -> AudioFormat:
    """Raises AudioValidationError (a ValueError) for anything that shouldn't
    reach a provider; the API converts that to a 422 exactly as it does for
    SourcePreparationError on a zip upload."""
    if not data:
        raise AudioValidationError("the recording is empty")
    # The route reads max_bytes + 1 (repos.py's bounded-read pattern), so an
    # oversize upload arrives here as exactly max_bytes + 1 bytes and is
    # rejected without the whole thing ever having been buffered.
    if len(data) > max_bytes:
        raise AudioValidationError(f"the recording exceeds the {max_bytes}-byte limit")
    fmt = sniff_audio_format(data)
    if fmt is None:
        raise AudioValidationError("unsupported audio format — expected webm, mp4, wav, or ogg")
    return fmt
