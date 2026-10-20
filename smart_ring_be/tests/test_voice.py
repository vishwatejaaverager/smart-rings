import io
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave

from fastapi.testclient import TestClient
import pytest

from src.backend.main import app
from src.services.speech import SpeechService, SpeechUnavailable


def recording(seconds=1):
    data = io.BytesIO()
    with wave.open(data, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * (16000 * seconds))
    return data.getvalue()


def test_transcription_uses_local_model_and_returns_editable_text():
    service = SpeechService()
    service._model = Mock()
    service._model.transcribe.return_value = ([SimpleNamespace(text=' Alert me if a person'),
                                             SimpleNamespace(text=' is carrying a box.')], SimpleNamespace(language='en'))
    with patch.object(service, 'status', return_value={'ready': True}):
        result = service.transcribe(recording())
    assert result['text'] == 'Alert me if a person is carrying a box.'
    assert result['language'] == 'en'
    assert service._model.transcribe.call_args.kwargs['vad_filter'] is True


@pytest.mark.parametrize('audio', [b'', b'not audio', recording(61)])
def test_invalid_recordings_are_rejected_before_model_load(audio):
    service = SpeechService()
    with pytest.raises(ValueError):
        service.transcribe(audio)
    assert service._model is None


def test_silence_does_not_become_a_command():
    service = SpeechService()
    service._model = Mock()
    service._model.transcribe.return_value = ([], SimpleNamespace(language='en'))
    with patch.object(service, 'status', return_value={'ready': True}):
        with pytest.raises(ValueError, match='No speech'):
            service.transcribe(recording())


def test_voice_endpoint_returns_text_without_arming_a_rule():
    with patch('src.backend.routes.voice.speech_service.transcribe', return_value={'text': 'Alert on a box', 'language': 'en'}) as transcribe, \
         patch('src.backend.routes.automations.rules_store.add_rule') as arm:
        response = TestClient(app).post('/api/v1/voice/transcribe', content=recording(), headers={'Content-Type': 'audio/wav'})
    assert response.status_code == 200
    assert response.json()['text'] == 'Alert on a box'
    transcribe.assert_called_once()
    arm.assert_not_called()


@pytest.mark.parametrize('error,status', [(SpeechUnavailable('Model unavailable'), 503), (ValueError('No speech'), 422)])
def test_voice_endpoint_reports_actionable_errors(error, status):
    with patch('src.backend.routes.voice.speech_service.transcribe', side_effect=error):
        response = TestClient(app).post('/api/v1/voice/transcribe', content=recording(), headers={'Content-Type': 'audio/wav'})
    assert response.status_code == status
    assert response.json()['detail'] == str(error)


def test_upload_limits_and_content_type():
    client = TestClient(app)
    assert client.post('/api/v1/voice/transcribe', content=b'x', headers={'Content-Type': 'text/plain'}).status_code == 415
    assert client.post('/api/v1/voice/transcribe', content=b'x' * (4 * 1024 * 1024 + 1), headers={'Content-Type': 'audio/wav'}).status_code == 413


def test_dashboard_client_sends_audio_to_local_transcription_route():
    import httpx
    from src.ui.client import DashboardClient
    response = httpx.Response(200, json={'text': 'Alert on a box'})
    with patch('src.ui.client.httpx.request', return_value=response) as request:
        assert DashboardClient('http://home').transcribe(b'wav')['text'] == 'Alert on a box'
    assert request.call_args.args == ('POST', 'http://home/api/v1/voice/transcribe')
    assert request.call_args.kwargs['content'] == b'wav'


def test_inference_cache_override_does_not_hide_existing_whisper_model(monkeypatch, tmp_path):
    from src.services import speech
    hub = tmp_path / '.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/complete'
    hub.mkdir(parents=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json'):
        (hub / name).write_bytes(b'fixture')
    monkeypatch.delenv('WHISPER_MODEL_PATH', raising=False)
    monkeypatch.setenv('HF_HUB_CACHE', str(tmp_path / 'unrelated-inference-cache'))
    monkeypatch.setattr(speech.Path, 'home', lambda: tmp_path)
    assert speech.model_directory() == hub
