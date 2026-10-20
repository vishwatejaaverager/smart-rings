"""Exercise the API endpoints used by query_history and set_deadline_alert."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.main import app
from src.storage.timeline_store import TimelineStore


def test_history_before_two_filters_person_events():
    store = TimelineStore()
    store.events = [
        {"event_type": "person_detected", "time_str": "13:30:00"},
        {"event_type": "person_detected", "time_str": "15:00:00"},
        {"event_type": "package_count_change", "time_str": "11:30:00"},
    ]
    with patch("src.backend.routes.automations.timeline_store", store):
        response = TestClient(app).get("/api/v1/automations/history", params={"end_time": "14:00", "event_type": "person"})
    assert response.status_code == 200
    assert response.json()["events"] == [store.events[0]]


def test_package_history_before_noon_returns_activity_not_delivery_proof():
    store = TimelineStore()
    store.events = [
        {"event_type": "package_count_change", "time_str": "11:00:00", "details": {"package_count": 1}},
        {"event_type": "package_count_change", "time_str": "13:00:00", "details": {"package_count": 2}},
    ]
    with patch("src.backend.routes.automations.timeline_store", store):
        response = TestClient(app).get("/api/v1/automations/history", params={"end_time": "12:00", "event_type": "package"})
    assert response.status_code == 200
    assert response.json()["events"] == [store.events[0]]


def test_deadline_tool_endpoint_arms_deadline():
    store = TimelineStore()
    with patch("src.backend.routes.automations.timeline_store", store):
        response = TestClient(app).post("/api/v1/automations/deadline", json={"expected_event": "package", "deadline_time": "16:00"})
    assert response.status_code == 201
    assert store.deadlines[0]["deadline_time"] == "16:00"
    assert not store.deadlines[0]["fired"]
