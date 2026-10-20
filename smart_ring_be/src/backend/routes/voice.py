import logging
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.services.speech import MAX_AUDIO_BYTES, SpeechUnavailable, speech_service

router = APIRouter(prefix="/voice", tags=["Voice"])
logger = logging.getLogger(__name__)


@router.get("/status")
async def voice_status():
    return speech_service.status()


@router.post("/transcribe")
async def transcribe_recording(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip() not in ("audio/wav", "audio/x-wav", "audio/wave"):
        raise HTTPException(415, "Please send a WAV recording.")
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "The recording is too large. Keep commands under 60 seconds.")
    try:
        return await run_in_threadpool(speech_service.transcribe, bytes(audio))
    except SpeechUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("Local transcription failed")
        raise HTTPException(503, "Transcription failed. Please try recording again.") from exc
