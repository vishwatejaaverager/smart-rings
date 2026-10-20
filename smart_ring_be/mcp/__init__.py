"""
Model Context Protocol (MCP) & Alexa Assistant Integration Layer.
Acts as an external interface adapter between AI assistants (Alexa, Claude, Cursor)
and the Smart Home Backend Engine.
"""
from mcp.alexa import alexa_assistant, AlexaAssistant
from mcp.server import mcp

__all__ = ["mcp", "alexa_assistant", "AlexaAssistant"]
