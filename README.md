# Smart Rings

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Describe what you want to watch for, and receive an alert on Fire TV when a supported condition matches.

> “Alert me if a person is carrying a box.”

## What it does

- Creates validated monitoring rules from typed or voice commands.
- Detects objects with YOLO, tracks people/vehicles with BoT-SORT, and checks visual conditions with local Qwen.
- Verifies package-count changes through Roboflow.
- Sends alerts to a Fire TV app and exposes six automation tools through MCP.

**Stack:** Python, FastAPI, Streamlit, OpenRouter, YOLO, BoT-SORT, Ollama/Qwen2.5-VL, Roboflow, faster-whisper, MCP, React Native, Amazon Vega, and Docker Compose.

## Setup

Requires Python 3.11, Ollama, and your own OpenRouter and Roboflow credentials. Fire TV development also requires Node.js 20+ and Vega tooling.

```bash
git clone https://github.com/vishwatejaaverager/smart-rings.git
cd smart-rings/smart_ring_be
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5vl:3b
```

Keep Ollama running. Create `smart_ring_be/.env`:

```dotenv
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=openai/gpt-4o-mini
ROBOFLOW_API_KEY=your_key
OLLAMA_URL=http://127.0.0.1:11434/api/generate
OLLAMA_MODEL=qwen2.5vl:3b
ALERT_WEBHOOK_URL=http://127.0.0.1:8000/webhook/alert
START_HEADLESS_LOOP=true
```

Provide these local assets; weights and footage are not included in Git:

- `smart_ring_be/models/unified_detector/weights/best.pt` — compatible detector with `package`, `person`, and `car` classes.
- `smart_ring_be/vids/theft.mp4` — authorized test footage.

For voice commands, run `python scripts/setup_voice.py` to prepare a local Whisper model.

## Run locally

From `smart_ring_be`, activate `.venv` in each terminal:

```bash
# Terminal 1: backend and monitoring
START_HEADLESS_LOOP=true python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000

# Terminal 2: command dashboard
FASTAPI_BASE_URL=http://127.0.0.1:8000 python -m streamlit run src/ui/dashboard.py --server.port 8501
```

Open **http://localhost:8501**. Type a command or record and review a voice command, then create the alert. API documentation: **http://localhost:8000/docs**.

## Run with Docker

Prepare `.env`, footage, and models first. The current Dockerfile also requires `smart_ring_be/yolo11s-seg.pt`. Ollama runs separately on the host; Compose connects through `host.docker.internal`.

From `smart_ring_be`:

```bash
docker compose up -d --build
docker compose logs -f backend
# Stop services:
docker compose down
```

Dashboard: **http://localhost:8501** · Backend: **http://localhost:8000**.

Use Docker or local processes for these ports. Rebuild after source changes. For container voice input, place Whisper under `models/whisper-base.en` and set `WHISPER_MODEL_PATH=/app/models/whisper-base.en` in `.env`.

## Connect MCP

With the backend running, start this separately from `smart_ring_be` using the Python environment:

```bash
MCP_BACKEND_URL=http://127.0.0.1:8000 python mcp/server.py
```

Connect an MCP client using **Streamable HTTP** at `http://127.0.0.1:8002/mcp`.

Tools: `create_automation`, `list_active_rules`, `remove_rule`, `clear_all_rules`, `query_history`, and `set_deadline_alert`.

See [MCP connection instructions](smart_ring_be/docs/mcp-connection.md) for client setup and remote authentication.

## Fire TV

Set the WebSocket URL in `firetv-app/src/App.tsx` to `ws://YOUR_BACKEND_LAN_IP:8000/ws/alerts`, then:

```bash
cd firetv-app
npm install
npm run build:debug
vega device install-app --device Simulator --dir . --buildType Debug
vega device launch-app --device Simulator --dir .
```

Requires a configured Vega simulator/device. Docker does not start MCP, Ollama, or the TV app.

## Current scope and next steps

This prototype monitors recorded footage. Ring preview integration is separate from the inference pipeline. Tracking IDs are temporary, and visual matches depend on model accuracy. Rules and history are in memory and reset when their process stops.

Next: persistent time-series storage for historical questions, live camera inference, durable deadline scheduling, and more reliable alert delivery.

See the [detailed project guide](PROJECT_GUIDE.md) for architecture, testing, troubleshooting, and the roadmap.

## License

[MIT](LICENSE). Third-party models, dependencies, datasets, and footage retain their own terms.
