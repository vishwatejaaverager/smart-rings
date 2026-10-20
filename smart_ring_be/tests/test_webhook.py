import numpy as np
import pytest
import httpx
from unittest.mock import patch, MagicMock

from src.notifications.webhook import send_alert_webhook, encode_frame_to_base64


def test_encode_frame_to_base64():
    # 100x100 3-channel dummy frame
    dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    b64_str = encode_frame_to_base64(dummy_frame)
    assert b64_str is not None
    assert isinstance(b64_str, str)
    assert len(b64_str) > 0


def test_webhook_not_configured():
    with patch.dict("os.environ", {}, clear=True):
        res = send_alert_webhook(
            payload={"event": "TEST_ALERT"},
            sync=True,
        )
        assert res is False


def test_webhook_dispatch_success():
    dummy_frame = np.zeros((50, 50, 3), dtype=np.uint8)
    fake_url = "https://example.com/api/webhook"

    with patch.dict(
        "os.environ",
        {
            "ALERT_WEBHOOK_URL": fake_url,
            "ALERT_WEBHOOK_SECRET": "secret-123",
            "INCLUDE_FRAME_IN_WEBHOOK": "true",
        },
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
            ok = send_alert_webhook(
                payload={"event": "PACKAGE_REMOVAL", "count": 2},
                frame=dummy_frame,
                sync=True,
            )
            assert ok is True
            assert mock_post.called

            call_args, call_kwargs = mock_post.call_args
            assert call_args[0] == fake_url
            payload = call_kwargs["json"]
            assert payload["event"] == "PACKAGE_REMOVAL"
            assert payload["count"] == 2
            assert "timestamp" in payload
            assert "snapshot" in payload
            assert "image_base64" in payload["snapshot"]

            headers = call_kwargs["headers"]
            assert headers["X-Webhook-Secret"] == "secret-123"
            assert headers["Authorization"] == "Bearer secret-123"


def test_webhook_error_handling():
    fake_url = "https://example.com/api/webhook"
    with patch.dict("os.environ", {"ALERT_WEBHOOK_URL": fake_url}):
        with patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
            ok = send_alert_webhook(
                payload={"event": "TEST_ALERT"},
                sync=True,
            )
            assert ok is False
