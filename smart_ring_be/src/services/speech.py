"""Local, lazily loaded Whisper transcription for short browser recordings."""
import importlib.util
import io
import os
from pathlib import Path
import threading
import wave

ROOT = Path(__file__).resolve().parents[2]
MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_SECONDS = 60


class SpeechUnavailable(RuntimeError):
    pass


def model_directory():
    configured = os.getenv("WHISPER_MODEL_PATH")
    if configured:
        return Path(configured).expanduser()
    bundled = ROOT / "models" / "whisper-base.en"
    if bundled.exists():
        return bundled
    hubs = [Path.home() / ".cache/huggingface/hub"]
    if os.getenv("HF_HOME"):
        hubs.insert(0, Path(os.environ["HF_HOME"]) / "hub")
    if os.getenv("HF_HUB_CACHE"):
        hubs.insert(0, Path(os.environ["HF_HUB_CACHE"]))
    # Reuse a complete existing local model; never download during an API request.
    for pattern in ("models--Systran--faster-whisper-base.en", "models--*--faster-whisper-large-v3-turbo"):
        for hub in hubs:
            for directory in sorted(hub.glob(pattern + "/snapshots/*")):
                if all((directory / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
                    return directory
    return bundled


class SpeechService:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def status(self):
        installed = importlib.util.find_spec("faster_whisper") is not None
        directory = model_directory()
        available = all((directory / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))
        detail = "Voice is ready. Record a short command."
        if not installed:
            detail = "Install the project's requirements to enable local Whisper."
        elif not available:
            detail = "Download the voice model with scripts/setup_voice.py, or set WHISPER_MODEL_PATH."
        return {"ready": installed and available, "runtime_installed": installed,
                "model_available": available, "loaded": self._model is not None,
                "engine": "faster-whisper", "detail": detail}

    def transcribe(self, audio):
        if not audio:
            raise ValueError("The recording is empty. Please record a command.")
        if len(audio) > MAX_AUDIO_BYTES:
            raise ValueError("The recording is too large. Keep commands under 60 seconds.")
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
                if wav.getnchannels() > 2 or wav.getsampwidth() != 2:
                    raise ValueError("Please use a 16-bit mono or stereo WAV recording.")
        except (wave.Error, EOFError, ZeroDivisionError) as exc:
            raise ValueError("This audio could not be read. Record a new command.") from exc
        if duration <= 0 or duration > MAX_AUDIO_SECONDS:
            raise ValueError("Record a command between 1 and 60 seconds long.")
        status = self.status()
        if not status["ready"]:
            raise SpeechUnavailable(status["detail"])
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                try:
                    self._model = WhisperModel(str(model_directory()), device="cpu",
                                               compute_type="int8", local_files_only=True)
                except Exception as exc:
                    raise SpeechUnavailable("The local Whisper model could not load. Check WHISPER_MODEL_PATH.") from exc
            segments, info = self._model.transcribe(
                io.BytesIO(audio), beam_size=3, vad_filter=True,
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise ValueError("No speech was heard. Please try again closer to the microphone.")
        return {"text": text, "language": info.language, "duration_seconds": round(duration, 2)}


speech_service = SpeechService()
