import pytest

from app.audio.errors import AudioValidationError
from app.audio.validation import sniff_audio_format, validate_audio_upload

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 20
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 20
OGG = b"OggS" + b"\x00" * 20


@pytest.mark.parametrize(
    ("data", "content_type", "extension"),
    [
        (WEBM, "audio/webm", "webm"),
        (MP4, "audio/mp4", "mp4"),
        (WAV, "audio/wav", "wav"),
        (OGG, "audio/ogg", "ogg"),
    ],
)
def test_sniffs_every_allowlisted_container(data: bytes, content_type: str, extension: str) -> None:
    fmt = sniff_audio_format(data)
    assert fmt is not None
    assert fmt.content_type == content_type
    assert fmt.extension == extension


def test_unknown_bytes_are_not_a_format() -> None:
    assert sniff_audio_format(b"hello world, definitely not audio") is None
    assert sniff_audio_format(b"") is None


def test_declared_content_type_is_ignored_in_favour_of_the_bytes() -> None:
    # The whole point of sniffing: a client can declare anything. Only what
    # the bytes actually are decides whether a provider sees them.
    fmt = validate_audio_upload(WAV, max_bytes=1024)
    assert fmt.content_type == "audio/wav"


def test_rejects_empty_and_oversize_uploads_before_sniffing() -> None:
    with pytest.raises(AudioValidationError, match="empty"):
        validate_audio_upload(b"", max_bytes=1024)
    with pytest.raises(AudioValidationError, match="exceeds"):
        validate_audio_upload(WEBM * 100, max_bytes=len(WEBM))


def test_rejects_an_unsupported_container() -> None:
    with pytest.raises(AudioValidationError, match="unsupported audio format"):
        validate_audio_upload(b"%PDF-1.4 not audio at all", max_bytes=1024)


def test_short_but_valid_header_is_accepted() -> None:
    # A real webm can begin with just the EBML magic; the sniff must not
    # demand more bytes than the shortest legitimate signature.
    assert validate_audio_upload(b"\x1a\x45\xdf\xa3", max_bytes=1024).extension == "webm"
