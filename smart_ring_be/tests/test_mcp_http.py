import json
import runpy
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from starlette.testclient import TestClient

SERVER = Path(__file__).resolve().parents[1] / "mcp/server.py"
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}


def load_server(monkeypatch, **env):
    for key in ("MCP_HOST", "MCP_PORT", "MCP_PUBLIC_URL", "MCP_AUTH_TOKEN", "MCP_BACKEND_URL", "MCP_ALLOWED_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return runpy.run_path(str(SERVER))


def rpc(client, method, params=None, request_id=1, headers=None):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": request_id,
                       "method": method, "params": params or {}}, headers={**HEADERS, **(headers or {})})


def initialize(client, headers=None):
    response = rpc(client, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                         "clientInfo": {"name": "hackathon-check", "version": "1.0"}}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


def test_initialization_and_all_tools_over_http(monkeypatch):
    server = load_server(monkeypatch)
    with TestClient(server["app"], base_url="http://127.0.0.1:8002") as client:
        initialize(client)
        response = rpc(client, "tools/list")
        assert response.status_code == 200
        assert {item["name"] for item in response.json()["result"]["tools"]} == {
            "create_automation", "list_active_rules", "remove_rule", "clear_all_rules", "query_history", "set_deadline_alert",
        }
        assert client.get("/health").json()["transport"] == "streamable-http"


def test_actual_tool_invocation_forwards_to_configured_backend(monkeypatch):
    server = load_server(monkeypatch, MCP_BACKEND_URL="http://preview:8001")
    response = httpx.Response(200, json={"rules": [{"rule": {"id": "carrying"}}]})
    with TestClient(server["app"], base_url="http://127.0.0.1:8002") as client:
        initialize(client)
        with patch("httpx.Client.get", return_value=response) as get:
            result = rpc(client, "tools/call", {"name": "list_active_rules", "arguments": {}}).json()["result"]
        assert not result.get("isError", False)
        assert json.loads(result["content"][0]["text"])[0]["rule"]["id"] == "carrying"
        get.assert_called_once_with("http://preview:8001/api/v1/automations/rules")


def test_create_rule_tool_forwards_prompt_and_returns_backend_rule(monkeypatch):
    server = load_server(monkeypatch)
    response = httpx.Response(201, json={"rule": {"id": "carrying", "source": "person"}})
    with TestClient(server["app"], base_url="http://127.0.0.1:8002") as client:
        initialize(client)
        with patch("httpx.Client.post", return_value=response) as post:
            result = rpc(client, "tools/call", {"name": "create_automation", "arguments": {"request": "Alert if a person carries a box"}}).json()["result"]
        assert json.loads(result["content"][0]["text"])["source"] == "person"
        assert post.call_args.kwargs["json"] == {"prompt": "Alert if a person carries a box"}


def test_untrusted_origin_and_host_are_rejected(monkeypatch):
    server = load_server(monkeypatch)
    with TestClient(server["app"], base_url="http://127.0.0.1:8002") as client:
        assert rpc(client, "tools/list", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert rpc(client, "tools/list", headers={"Host": "untrusted.example"}).status_code == 421


def test_backend_failure_is_a_tool_error_not_a_successful_text_result(monkeypatch):
    server = load_server(monkeypatch)
    with TestClient(server["app"], base_url="http://127.0.0.1:8002") as client:
        initialize(client)
        with patch("httpx.Client.get", side_effect=httpx.ConnectError("backend offline")):
            result = rpc(client, "tools/call", {"name": "list_active_rules", "arguments": {}}).json()["result"]
        assert result["isError"] is True
        assert "backend offline" in result["content"][0]["text"]


def test_public_endpoint_requires_and_accepts_reviewer_token(monkeypatch):
    server = load_server(monkeypatch, MCP_PUBLIC_URL="https://mcp.example.com/mcp", MCP_AUTH_TOKEN="reviewer-test-token")
    with TestClient(server["app"], base_url="https://mcp.example.com") as client:
        assert rpc(client, "tools/list").status_code == 401
        assert rpc(client, "tools/list", headers={"Authorization": "Bearer wrong"}).status_code == 401
        initialize(client, headers={"Authorization": "Bearer reviewer-test-token"})
        result = rpc(client, "tools/list", headers={"Authorization": "Bearer reviewer-test-token"})
        assert result.status_code == 200


@pytest.mark.parametrize("env", [{"MCP_HOST": "0.0.0.0"}, {"MCP_PUBLIC_URL": "https://mcp.example.com/mcp"}])
def test_public_startup_without_auth_fails(monkeypatch, env):
    with pytest.raises(ValueError, match="MCP_AUTH_TOKEN"):
        load_server(monkeypatch, **env)
