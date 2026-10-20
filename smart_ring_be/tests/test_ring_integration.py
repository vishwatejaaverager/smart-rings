import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from starlette.testclient import TestClient

from src.backend.main import app
from src.integrations.ring.client import RingAPIError, RingClient
from src.integrations.ring.stream import RingVideoStream


def test_ring_client_base_url():
    # 1. Default base URL
    with patch.dict(os.environ, {"RING_ACCESS_TOKEN": "test_token"}, clear=False):
        os.environ.pop("RING_API_BASE_URL", None)
        client = RingClient()
        assert client.base_url == "https://api.amazonvision.com/v1"
        assert client.access_token == "test_token"

    # 2. Base URL from env variable
    with patch.dict(
        os.environ,
        {
            "RING_ACCESS_TOKEN": "test_token",
            "RING_API_BASE_URL": "https://api.customvision.com/v1/",
        },
    ):
        client = RingClient()
        assert client.base_url == "https://api.customvision.com/v1"


def test_ring_client_http_401_error():
    client = RingClient(access_token="invalid_token")

    mock_resp = httpx.Response(
        status_code=401,
        request=httpx.Request("GET", "https://api.amazonvision.com/v1/devices"),
        text="Unauthorized token",
    )

    with patch.object(client.http, "get", new=AsyncMock(return_value=mock_resp)):
        async def run():
            try:
                await client.get_devices()
                assert False, "Should have raised RingAPIError"
            except RingAPIError as exc:
                assert exc.status_code == 401
                assert "401" in str(exc)

        asyncio.run(run())


def test_stream_whep_and_session_cleanup():
    async def run():
        stream = RingVideoStream(
            device_id="test_device_123",
            token="test_token",
            base_url="https://api.amazonvision.com/v1",
        )
        assert stream.device_id == "test_device_123"
        assert stream.base_url == "https://api.amazonvision.com/v1"

        mock_post_resp = httpx.Response(
            status_code=201,
            headers={
                "Location": "https://api.amazonvision.com/v1/devices/test_device_123/media/streaming/whep/sessions/session_xyz"
            },
            text="v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n",
            request=httpx.Request("POST", "http://test"),
        )

        mock_delete_resp = httpx.Response(
            status_code=200,
            request=httpx.Request("DELETE", "http://test"),
        )

        recorded_requests = []

        class DummyAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, headers=None, content=None):
                recorded_requests.append(("POST", url, headers, content))
                return mock_post_resp

            async def delete(self, url, headers=None):
                recorded_requests.append(("DELETE", url, headers))
                return mock_delete_resp

        with patch("httpx.AsyncClient", DummyAsyncClient):
            # Patch aiortc setRemoteDescription to avoid real SDP parsing issues in mock
            with patch("src.integrations.ring.stream.RTCPeerConnection.setRemoteDescription", new=AsyncMock()):
                res = await stream.start()
                assert res["status"] == "connecting"
                assert res["device_id"] == "test_device_123"
                assert stream.session_url == "https://api.amazonvision.com/v1/devices/test_device_123/media/streaming/whep/sessions/session_xyz"

                # Verify POST endpoint URL
                post_call = [r for r in recorded_requests if r[0] == "POST"][0]
                expected_whep = "https://api.amazonvision.com/v1/devices/test_device_123/media/streaming/whep/sessions"
                assert post_call[1] == expected_whep
                assert post_call[2]["Authorization"] == "Bearer test_token"

                # Now test stop() sends DELETE to session_url
                await stream.stop()
                delete_call = [r for r in recorded_requests if r[0] == "DELETE"][0]
                assert delete_call[1] == stream.session_url or delete_call[1] == "https://api.amazonvision.com/v1/devices/test_device_123/media/streaming/whep/sessions/session_xyz"
                assert delete_call[2]["Authorization"] == "Bearer test_token"
                assert stream.session_url is None
                assert stream.pc is None

    asyncio.run(run())


def test_stream_auto_discovery():
    async def run():
        # Do not supply device_id
        stream = RingVideoStream(
            device_id=None,
            token="test_token",
            base_url="https://api.amazonvision.com/v1",
        )

        # Mock RingClient.get_devices()
        mock_devices_response = {
            "data": [
                {"id": "auto_discovered_device_999", "name": "Front Porch"}
            ]
        }

        with patch.object(
            RingClient, "get_devices", new=AsyncMock(return_value=mock_devices_response)
        ), patch.object(RingClient, "close", new=AsyncMock()):
            discovered = await stream.discover_device_id()
            assert discovered == "auto_discovered_device_999"

    asyncio.run(run())


def test_streams_api_routes():
    client = TestClient(app)

    # 1. Test POST /api/v1/streams/start with 401 from Ring
    with patch(
        "src.backend.routes.streams.RingVideoStream.start",
        side_effect=RingAPIError("Ring API HTTP 401: Unauthorized", status_code=401),
    ):
        resp = client.post("/api/v1/streams/start", json={"device_id": "cam_1"})
        assert resp.status_code == 401
        assert "Ring access token is expired or invalid" in resp.json()["detail"]

    # 2. Test POST /api/v1/streams/start with no devices found
    with patch(
        "src.backend.routes.streams.RingVideoStream.start",
        side_effect=ValueError("No devices found on the Ring account"),
    ):
        resp = client.post("/api/v1/streams/start?device_id=")
        assert resp.status_code == 404
        assert "No Ring devices found on this account" in resp.json()["detail"]

    # 3. Test POST /api/v1/streams/start with device_id via query param
    with patch(
        "src.backend.routes.streams.RingVideoStream.start",
        new=AsyncMock(return_value={"status": "connecting", "device_id": "query_dev_1"}),
    ):
        resp = client.post("/api/v1/streams/start?device_id=query_dev_1")
        assert resp.status_code == 200
        assert resp.json()["device_id"] == "query_dev_1"

    # Stop stream cleanup
    client.post("/api/v1/streams/stop")

    # 4. Test POST /api/v1/streams/start with device_id via body
    with patch(
        "src.backend.routes.streams.RingVideoStream.start",
        new=AsyncMock(return_value={"status": "connecting", "device_id": "body_dev_2"}),
    ):
        resp = client.post("/api/v1/streams/start", json={"device_id": "body_dev_2"})
        assert resp.status_code == 200
        assert resp.json()["device_id"] == "body_dev_2"

    client.post("/api/v1/streams/stop")


if __name__ == "__main__":
    test_ring_client_base_url()
    print("✅ test_ring_client_base_url passed")
    test_ring_client_http_401_error()
    print("✅ test_ring_client_http_401_error passed")
    test_stream_whep_and_session_cleanup()
    print("✅ test_stream_whep_and_session_cleanup passed")
    test_stream_auto_discovery()
    print("✅ test_stream_auto_discovery passed")
    test_streams_api_routes()
    print("✅ test_streams_api_routes passed")
    print("\n🎉 ALL TESTS PASSED!")
