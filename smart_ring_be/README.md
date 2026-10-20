# Smart Rings backend

Smart Rings turns typed, browser-recorded, or MCP-delivered instructions into
camera-monitoring rules and forwards matched events to a Fire TV application.

The **[main project README](../README.md)** is the canonical guide. It covers:

- Project scope, current capabilities, and known limitations.
- Architecture, LLM rule generation, tracking, and alert delivery.
- Python, Ollama, Whisper, model assets, and environment configuration.
- Backend, dashboard, desktop monitor, Fire TV, and MCP startup instructions.
- Ring device/video integration and its current boundary with monitoring.
- Data lifecycle, test commands, and troubleshooting.
- The open-source roadmap: durable time-series storage, natural-language temporal
  queries, time-aware automation, live cameras, and reliable delivery.

Start with **[installation and configuration](../README.md#installation-and-configuration)**,
then follow **[run the project](../README.md#run-the-project)**. Commands in those
sections identify whether to run from this directory or the repository root.

For MCP-specific setup, see **[MCP connection instructions](docs/mcp-connection.md)**.
For planned persistence and historical questions, see **[future scope](../README.md#future-scope-and-open-source-roadmap)**.
