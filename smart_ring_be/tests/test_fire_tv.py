import pytest
from fastapi.testclient import TestClient
from src.backend.main import app

client = TestClient(app)


def test_fire_tv_status():
    resp = client.get("/fire-tv/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "active_fire_tv_clients" in data
    assert "last_broadcast" in data


def test_webhook_ingestion_and_broadcast():
    # Test WebSocket connection and message delivery
    with client.websocket_connect("/ws/alerts") as ws:
        # Check that active client is registered
        status_resp = client.get("/fire-tv/status")
        assert status_resp.json()["active_fire_tv_clients"] == 1

        # Post an alert to the webhook
        webhook_payload = {
            "event": "PACKAGE_REMOVAL",
            "timestamp": 1729220000.123,
            "details": {
                "previous_count": 3,
                "current_count": 1,
                "packages_removed": 2,
                "mode": "hybrid",
            },
            "snapshot": {
                "format": "jpeg",
                "image_base64": "dummyBase64Data",
            },
        }

        resp = client.post("/webhook/alert", json=webhook_payload)
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "delivered"
        assert result["recipients"] == 1

        # Verify Fire TV WebSocket client received the normalized event
        data = ws.receive_json()
        assert data["rule"] == "Package Removal Alert"
        assert data["action"] == "show_alert_on_fire_tv"
        assert "2 package(s) removed" in data["message"]
        assert data["origin"] == "live_webhook"
        assert data["snapshot"]["image_base64"] == "dummyBase64Data"
