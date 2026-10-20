#!/usr/bin/env python3
"""
Smart Home AI - FastMCP Server
Exposes smart home defense tools for AI assistants (Alexa, Claude Desktop, Cursor)
over MCP Streamable HTTP (default), with optional legacy stdio support.
Connects directly to the FastAPI backend over clean HTTP REST APIs.
"""
import os
import sys
import argparse
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# Prevent local folder name 'mcp' from shadowing installed pip 'mcp' package
_cur_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_cur_dir)
_saved_paths = list(sys.path)
sys.path = [p for p in sys.path if os.path.abspath(p) not in (_cur_dir, _parent_dir)]
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
sys.path = _saved_paths

import json
import httpx
from starlette.responses import JSONResponse

HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8002"))
AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")
PUBLIC_URL = os.getenv("MCP_PUBLIC_URL", "").rstrip("/")
allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
if PUBLIC_URL:
    public = urlsplit(PUBLIC_URL)
    if public.scheme != "https" or not public.hostname or public.username or public.password:
        raise ValueError("MCP_PUBLIC_URL must be an HTTPS URL without credentials")
    allowed_hosts.append(public.netloc)
    allowed_origins.append(f"{public.scheme}://{public.netloc}")
if (PUBLIC_URL or HOST not in ("127.0.0.1", "localhost", "::1")) and not AUTH_TOKEN:
    raise ValueError("Set MCP_AUTH_TOKEN before exposing MCP outside localhost")
allowed_origins.extend(origin.strip() for origin in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if origin.strip())

mcp = FastMCP(
    "Smart Home Alexa AI", host=HOST, port=PORT,
    instructions="Create and manage home-monitoring rules, query observed events, and schedule deadlines. "
                 "Use query_history for past-event questions and set_deadline_alert for missed-event deadlines.",
    streamable_http_path="/mcp", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
    ),
)

BACKEND_BASE_URL = os.getenv("MCP_BACKEND_URL", os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:8000")).rstrip("/") + "/api/v1/automations"
BACKEND_RULES_URL = f"{BACKEND_BASE_URL}/rules"


@mcp.tool()
def create_automation(request: str) -> str:
    """
    Convert a natural language smart-home prompt (e.g. 'Alert me if package disappears')
    into an active defense rule in the backend monitoring engine.
    """
    try:
        with httpx.Client(timeout=65.0) as client:
            resp = client.post(BACKEND_RULES_URL, json={"prompt": request})
            if resp.status_code in (200, 201):
                data = resp.json()
                return json.dumps(data.get("rule", {}), indent=2)
            raise ToolError(f"Backend error ({resp.status_code}): {resp.text}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Backend request failed: {exc}") from exc


@mcp.tool()
def list_active_rules() -> str:
    """List all currently armed defense rules in the monitoring engine."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(BACKEND_RULES_URL)
            if resp.status_code == 200:
                entries = resp.json().get("rules", [])
                return json.dumps(entries, indent=2)
            raise ToolError(f"Backend error ({resp.status_code}): {resp.text}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Backend request failed: {exc}") from exc


@mcp.tool()
def remove_rule(rule_id: str) -> str:
    """Remove an armed defense rule from the engine by its ID."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.delete(f"{BACKEND_RULES_URL}/{rule_id}")
            if resp.status_code == 200:
                return f"Rule '{rule_id}' removed successfully."
            raise ToolError(f"Backend error ({resp.status_code}): {resp.text}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Backend request failed: {exc}") from exc


@mcp.tool()
def clear_all_rules() -> str:
    """Clear all armed rules from the monitoring engine."""
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.delete(BACKEND_RULES_URL)
            if resp.status_code == 200:
                return "All rules cleared from monitoring engine."
            raise ToolError(f"Backend error ({resp.status_code}): {resp.text}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Backend request failed: {exc}") from exc


@mcp.tool()
def query_history(
    start_time: str = "",
    end_time: str = "",
    object_type: str = "",
) -> str:
    """
    Query past camera events that already happened (e.g. 'Did anyone come before 2:00 PM?').
    Args:
        start_time: Optional start time filter (e.g. '13:00' or '1:00 PM')
        end_time: Optional end time filter (e.g. '14:00' or '2:00 PM')
        object_type: Optional filter by object/event ('person', 'package', 'car')
    """
    try:
        params = {}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if object_type:
            params["event_type"] = object_type

        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{BACKEND_BASE_URL}/history", params=params)
            if resp.status_code == 200:
                events = resp.json().get("events", [])
                if not events:
                    return f"No events recorded matching criteria (start: {start_time}, end: {end_time}, type: {object_type})."
                return json.dumps(events, indent=2)
            raise ToolError(f"Backend error ({resp.status_code}): {resp.text}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Backend request failed: {exc}") from exc


@mcp.tool()
def set_deadline_alert(
    expected_event: str,
    deadline_time: str,
    alert_message: str = "",
) -> str:
    """
    Set an alert for an expected event that MUST happen before a specific time.
    Fires if the event DID NOT occur by the deadline (e.g. 'Alert if package did not arrive by 2:00 PM').
    Args:
        expected_event: The event to wait for ('package', 'person', 'delivery')
        deadline_time: Target deadline time string (e.g. '14:00' or '2:00 PM')
        alert_message: Custom alert message to send to Fire TV if deadline passes
    """
    try:
        payload = {
            "expected_event": expected_event,
            "deadline_time": deadline_time,
            "message": alert_message or f"Alert: Expected {expected_event} did not occur by {deadline_time}",
        }
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(f"{BACKEND_BASE_URL}/deadline", json=payload)
            if resp.status_code in (200, 201):
                return f"Deadline alert armed: Watching for '{expected_event}' before {deadline_time}."
            raise ToolError(f"Backend error ({resp.status_code}): {resp.text}")
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Backend request failed: {exc}") from exc


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok", "transport": "streamable-http", "protocol": "2025-11-25"})


class BearerTokenMiddleware:
    """Optional reviewer token without buffering MCP streaming responses."""
    def __init__(self, app, token):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            authorization = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
            scheme, _, supplied = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(supplied.encode(), self.token.encode()):
                response = JSONResponse({"error": "A valid bearer token is required"}, status_code=401,
                                        headers={"WWW-Authenticate": 'Bearer realm="smart-ring-mcp"'})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = mcp.streamable_http_app()
if AUTH_TOKEN:
    app.add_middleware(BearerTokenMiddleware, token=AUTH_TOKEN)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart Ring MCP server")
    parser.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run(app, host=HOST, port=PORT)
