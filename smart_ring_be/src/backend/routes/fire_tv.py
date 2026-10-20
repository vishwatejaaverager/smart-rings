import asyncio
import json
import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, Response, status

router = APIRouter(tags=["Fire TV"])


class ConnectionManager:
    """Manages active WebSocket connections to Fire TV devices/apps."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.last_event: Optional[Dict[str, Any]] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"📺 [Fire TV WS] Client connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"📺 [Fire TV WS] Client disconnected. Total active: {len(self.active_connections)}")

    async def broadcast(self, event: Dict[str, Any]):
        self.last_event = event
        disconnected = []
        message_str = json.dumps(event)

        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception as exc:
                print(f"⚠️ [Fire TV WS] Error sending to client: {exc}")
                disconnected.append(connection)

        for conn in disconnected:
            self.disconnect(conn)

        print(
            f"📡 [Fire TV WS] Broadcasted alert to {len(self.active_connections)} client(s): {event.get('message')}"
        )


manager = ConnectionManager()


@router.websocket("/ws/alerts")
async def websocket_alerts_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for Fire TV app.
    Broadcasts real-time security & package theft alerts directly to the TV.
    """
    await manager.connect(websocket)
    try:
        while True:
            # Keep socket alive; handle any messages or heartbeat pings from Fire TV
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "timestamp": time.time()}))
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:
        print(f"⚠️ [Fire TV WS] Unexpected error on socket: {exc}")
        manager.disconnect(websocket)


@router.post("/webhook/alert")
@router.post("/api/v1/webhook/alert")
async def receive_alert_webhook(request: Request):
    """
    Receives outgoing alert webhooks from package detection & rule monitors.
    Normalizes the payload and pushes real-time alert events over WebSocket to Fire TV.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        return Response(
            content=json.dumps({"error": f"Invalid JSON payload: {exc}"}),
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )

    # 1. Normalize alert message and rule title
    details = payload.get("details", {})
    event_type = payload.get("event") or payload.get("event_type") or "package_removal"
    
    if "alert" in payload:
        # Fired from rule evaluator (e.g. realtime_demo.py)
        raw_rule_id = payload.get("rule_id", "Package Alert")
        rule_title = raw_rule_id.replace("_", " ").title()
        message = payload.get("alert")
        # Format technical threshold messages into clear, conversational TV alerts
        if "arrival threshold reached:" in message.lower():
            message = "📦 " + message.split(":", 1)[-1].strip()
        elif "drop" in message.lower() or "removal" in message.lower() or "theft" in message.lower():
            if not message.startswith("🚨"):
                message = "🚨 " + message
    elif details.get("packages_removed"):
        # Fired from package removal detection
        removed = details.get("packages_removed")
        prev = details.get("previous_count", "")
        curr = details.get("current_count", "")
        rule_title = "Package Removal Alert"
        message = f"🚨 {removed} package(s) removed from front porch! (Count: {prev} -> {curr})"
    elif details.get("alert_message"):
        rule_title = payload.get("rule", "Package Alert").replace("_", " ").title()
        message = details.get("alert_message")
    else:
        rule_title = payload.get("rule", "Security Alert").replace("_", " ").title()
        message = payload.get("message", "Package activity detected at front door")

    # 2. Format timestamp (Fire TV app formats millisecond timestamps)
    raw_ts = payload.get("timestamp", time.time())
    if isinstance(raw_ts, (int, float)):
        ts_ms = int(raw_ts * 1000) if raw_ts < 1e11 else int(raw_ts)
    else:
        ts_ms = int(time.time() * 1000)

    # 3. Create normalized Fire TV event
    fire_tv_event = {
        "rule": rule_title,
        "action": "show_alert_on_fire_tv",
        "message": message,
        "event_type": event_type,
        "device_id": payload.get("device_id", "front_door_cam"),
        "timestamp": ts_ms,
        "origin": "live_webhook",
        "details": details,
    }

    # Pass snapshot if available
    if "snapshot" in payload:
        fire_tv_event["snapshot"] = payload["snapshot"]

    # 4. Broadcast to all active Fire TV apps
    await manager.broadcast(fire_tv_event)

    return {
        "status": "delivered",
        "recipients": len(manager.active_connections),
        "event": fire_tv_event["rule"],
        "message": fire_tv_event["message"],
    }


@router.get("/fire-tv/status")
@router.get("/api/v1/fire-tv/status")
async def fire_tv_status():
    """Returns active Fire TV connections count and last broadcast event."""
    client_ips = [
        f"{getattr(c.client, 'host', 'unknown')}:{getattr(c.client, 'port', '')}"
        for c in manager.active_connections
    ]
    return {
        "active_fire_tv_clients": len(manager.active_connections),
        "client_ips": client_ips,
        "last_broadcast": manager.last_event,
    }
