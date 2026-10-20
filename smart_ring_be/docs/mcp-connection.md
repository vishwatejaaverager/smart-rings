# Connect to Smart Ring over MCP Streamable HTTP

The server uses the official MCP Python SDK 1.x, supports protocol `2025-11-25`,
and serves Streamable HTTP at `/mcp`. This replaces the old stdio-only default.
It is a separate process from FastAPI and shares rules through the backend API.

## Local launch

From `smart_ring_be`, with requirements installed:

```sh
# Terminal 1: backend, unless already running.
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000

# Terminal 2: MCP server.
.venv/bin/python mcp/server.py

# Terminal 3: real client handshake, discovery, and a read-only tool call.
.venv/bin/python scripts/check_mcp.py --url http://127.0.0.1:8002/mcp
```

Local endpoint: `http://127.0.0.1:8002/mcp`.
Health endpoint: `http://127.0.0.1:8002/health`.
`GET /mcp` returning 405 is allowed because this server uses JSON responses rather
than opening a standalone SSE stream; initialization uses an HTTP POST.

Set `MCP_BACKEND_URL=http://127.0.0.1:8001` when using the preview backend.
Use `MCP_HOST` and `MCP_PORT` to change the listener. Optional legacy stdio:
`.venv/bin/python mcp/server.py --transport stdio`.

## Remote reviewer connection

A local endpoint is not accessible from a judge's computer. Deploy behind HTTPS
using your hosting/reverse proxy, or an explicitly configured tunnel that stays
running during evaluation. No public tunnel is created automatically.

Set these variables on the server before starting it:

```sh
MCP_PUBLIC_URL=https://YOUR-ACTUAL-HOST/mcp
MCP_AUTH_TOKEN=YOUR-GENERATED-REVIEWER-TOKEN
MCP_BACKEND_URL=http://YOUR-INTERNAL-BACKEND:8000
```

The example host/token are placeholders. Generate a token with
`python -c 'import secrets; print(secrets.token_urlsafe(32))'`, then keep it outside
the repository. Share the reviewer token privately in testing instructions.
Public/non-loopback configuration requires a token. DNS-rebinding/Origin checks
remain enabled; `MCP_PUBLIC_URL` allowlists the exact public host. For a browser
client using another origin, set `MCP_ALLOWED_ORIGINS` to its exact origin.

Reviewer client settings:

- Transport: **Streamable HTTP**.
- URL: your actual `https://…/mcp` endpoint.
- Header: `Authorization: Bearer <reviewer token>`.

This is a manually provisioned bearer token, not an OAuth authorization server.
Use a client supporting custom headers; clients requiring OAuth discovery need
additional authorization-server integration.

Test the public URL using `scripts/check_mcp.py` with the token in the client's
environment before submitting it. The checker verifies the negotiated protocol,
all six tools, and an actual backend read. Do not submit localhost or an expired
tunnel URL. Keep the MCP server, backend, required model services, and any tunnel
available for the judging period. Repository/video access is separately required.

For a container deployment, bind `MCP_HOST=0.0.0.0`, publish the selected port, set
the reviewer token, and use the backend's internal service hostname. The default
loopback listener and host backend URL are intended for local development.

## Tools

`create_automation`, `list_active_rules`, `remove_rule`, `clear_all_rules`,
`query_history`, and `set_deadline_alert` retain their existing schemas.
Creating an alert still requires the backend/model configuration; monitoring and
TV delivery must be running separately. A healthy MCP process does not establish
camera or TV connectivity.
