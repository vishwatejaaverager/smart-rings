"""Read-only check of an actual MCP Streamable HTTP connection.

Run: python scripts/check_mcp.py --url http://127.0.0.1:8002/mcp
For a protected endpoint set MCP_AUTH_TOKEN in the client's environment.
"""
import argparse
import asyncio
import os
import sys

# Avoid the project's legacy mcp/ directory shadowing the installed SDK.
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path = [p for p in sys.path if os.path.abspath(p or ".") != root]
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(url):
    headers = {}
    if os.getenv("MCP_AUTH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["MCP_AUTH_TOKEN"]
    async with httpx.AsyncClient(headers=headers, timeout=90) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert initialized.protocolVersion == "2025-11-25", initialized.protocolVersion
                print("Negotiated protocol:", initialized.protocolVersion)
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                expected = {"create_automation", "list_active_rules", "remove_rule", "clear_all_rules", "query_history", "set_deadline_alert"}
                assert expected <= names, names
                print("Discovered tools:", ", ".join(sorted(names)))
                result = await session.call_tool("list_active_rules", {})
                text = "\n".join(getattr(item, "text", "") for item in result.content)
                assert not result.isError, text
                # Do not mistake a successful MCP envelope for a working backend.
                import json
                assert isinstance(json.loads(text), list), text
                print("list_active_rules succeeded. No rules were changed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8002/mcp")
    asyncio.run(check(parser.parse_args().url))
