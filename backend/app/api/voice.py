"""GET /voice/capabilities — the only route in this module, and the only
reason it exists.

The audio endpoints themselves live next to the resources they act on
(api/study_guides.py for section speech, api/attempts.py for transcription
and feedback speech), because that's where their ownership helpers already
are. What doesn't belong to any one resource is "is voice on at all?" —
which the frontend has to know before it renders a mic button or a read-
aloud control, so a learner is never shown a control that 503s on click.

Booleans only, no backend names: which backend a deployment uses is
operator config, and the UI is deliberately kept ignorant of it (see
app/audio/dependencies.py).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.audio.dependencies import speech_status, transcription_status
from app.db.models import User

router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceCapabilitiesOut(BaseModel):
    transcription: bool
    speech: bool


@router.get("/capabilities", response_model=VoiceCapabilitiesOut)
async def get_voice_capabilities(_current_user: User = Depends(get_current_user)) -> VoiceCapabilitiesOut:
    return VoiceCapabilitiesOut(
        transcription=transcription_status().enabled,
        speech=speech_status().enabled,
    )
