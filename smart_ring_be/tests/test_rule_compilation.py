import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from src.evaluator.llm_parser import (
    RuleCompilationError, UnsupportedIntentError, extract_json_rules,
    parse_prompt_to_rule, parse_prompt_to_rules,
)
from src.evaluator.models import Rule


VISUAL = {
    "id": "blue_shirt", "source": "person",
    "vlm_query": "Is this person wearing a blue shirt? Answer YES or NO.",
    "expected_answer": "YES", "alert": {"fire": "every_time", "cooldown_seconds": 5},
}


def completion(content):
    return httpx.Response(200, json={"id": "test-response", "choices": [
        {"message": {"content": content}, "finish_reason": "stop"}
    ]})


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-real-key")


@pytest.mark.parametrize("bad", ["[]", "", "not json", '{"rules": []}',
                                      '{"id":"bad","alert":{"fire":"every_time"}}'])
def test_empty_or_invalid_output_is_repaired(bad):
    good = json.dumps({"rules": [VISUAL], "unsupported_reason": None})
    with patch("src.evaluator.llm_parser.httpx.post", side_effect=[completion(bad), completion(good)]) as post:
        assert parse_prompt_to_rule("Alert me if a person in a blue shirt approaches the door").source == "person"
        assert post.call_count == 2
        payload = post.call_args.kwargs["json"]
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["messages"][1]["content"].endswith("approaches the door'")


def test_repeated_empty_output_fails_as_provider_error():
    with patch("src.evaluator.llm_parser.httpx.post", return_value=completion("[]")) as post:
        with pytest.raises(RuleCompilationError, match="twice"):
            parse_prompt_to_rule("Alert on a blue shirt")
        assert post.call_count == 2


def test_history_and_deadline_rejections_are_not_retried():
    body = json.dumps({"rules": [], "unsupported_reason": "Use query_history or set_deadline_alert"})
    with patch("src.evaluator.llm_parser.httpx.post", return_value=completion(body)) as post:
        with pytest.raises(UnsupportedIntentError, match="query_history"):
            parse_prompt_to_rule("Did anyone come before 2 PM?")
        assert post.call_count == 1


def test_provider_failure_is_not_retried_or_exposed():
    with patch("src.evaluator.llm_parser.httpx.post", return_value=httpx.Response(402, text="private provider details")) as post:
        with pytest.raises(RuleCompilationError, match="HTTP 402") as error:
            parse_prompt_to_rules("Alert if a package disappears")
        assert "private provider details" not in str(error.value)
        assert post.call_count == 1


def test_blank_prompt_does_not_call_provider():
    with patch("src.evaluator.llm_parser.httpx.post") as post:
        with pytest.raises(ValueError, match="nonempty"):
            parse_prompt_to_rules(" ")
        post.assert_not_called()


@pytest.mark.parametrize("body", [json.dumps([VISUAL]), json.dumps(VISUAL),
                                      "```json\n" + json.dumps([VISUAL]) + "\n```"])
def test_legacy_json_formats_remain_supported(body):
    assert extract_json_rules(body) == [VISUAL]


@pytest.mark.parametrize("rule", [
    {"id": "bad", "source": "unknown", "alert": {"fire": "every_time"}},
    {"id": "bad", "source": "package_count", "derive": {"type": "delta"}, "alert": {"fire": "every_time"}},
    {"id": "bad", "source": "person", "alert": {"fire": "every_time"}},
])
def test_incomplete_rules_cannot_be_armed(rule):
    with pytest.raises(ValidationError):
        Rule.model_validate(rule)


def test_api_recovers_and_returns_201_without_starting_monitor():
    from fastapi.testclient import TestClient
    from src.backend.main import app
    from src.storage.rules_store import rules_store
    good = json.dumps({"rules": [VISUAL], "unsupported_reason": None})
    try:
        with patch("src.evaluator.llm_parser.httpx.post", side_effect=[completion("[]"), completion(good)]):
            response = TestClient(app).post("/api/v1/automations/rules", json={"prompt": "Alert on a blue shirt"})
        assert response.status_code == 201
        assert response.json()["rule"]["source"] == "person"
        assert response.json()["warnings"]
    finally:
        rules_store.remove_rule(VISUAL["id"])


@pytest.mark.parametrize("payload,status", [({}, 400), ({"prompt": "Alert on a blue shirt"}, 502),
                                             ({"rule": {"id": "broken"}}, 400)])
def test_api_distinguishes_bad_requests_from_empty_provider_output(payload, status):
    from fastapi.testclient import TestClient
    from src.backend.main import app
    with patch("src.evaluator.llm_parser.httpx.post", return_value=completion("[]")):
        assert TestClient(app).post("/api/v1/automations/rules", json=payload).status_code == status
