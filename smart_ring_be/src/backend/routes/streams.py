
import asyncio
from typing import Optional

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.integrations.ring.client import RingAPIError
from src.integrations.ring.stream import RingVideoStream


class StartStreamRequest(BaseModel):
    device_id: Optional[str] = None


router = APIRouter(prefix="/streams", tags=["Ring Video"])

_stream: Optional[RingVideoStream] = None
_stream_lock = asyncio.Lock()


@router.post("/start")
async def start_stream(
    device_id: Optional[str] = None,
    payload: Optional[StartStreamRequest] = None,
):
    global _stream

    target_device_id = (
        payload.device_id if payload and payload.device_id else None
    ) or device_id

    async with _stream_lock:
        if _stream is not None:
            return {"status": "already_started", "device_id": _stream.device_id}

        candidate = None
        try:
            candidate = RingVideoStream(device_id=target_device_id)
            result = await candidate.start()
            _stream = candidate
            return result
        except RingAPIError as exc:
            if candidate is not None:
                await candidate.stop()
            if exc.status_code == 401:
                raise HTTPException(
                    status_code=401,
                    detail="Ring access token is expired or invalid. Please update RING_ACCESS_TOKEN in .env.",
                ) from exc
            if exc.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail=f"Ring device not found: {exc}",
                ) from exc
            raise HTTPException(
                status_code=502,
                detail=f"Ring API error: {exc}",
            ) from exc
        except ValueError as exc:
            if candidate is not None:
                await candidate.stop()
            err_msg = str(exc)
            if "No devices found" in err_msg:
                raise HTTPException(
                    status_code=404,
                    detail="No Ring devices found on this account. Please specify a device_id or grant device permissions.",
                ) from exc
            if "RING_ACCESS_TOKEN" in err_msg:
                raise HTTPException(
                    status_code=500,
                    detail="RING_ACCESS_TOKEN is missing from .env",
                ) from exc
            raise HTTPException(
                status_code=400,
                detail=err_msg,
            ) from exc
        except Exception as exc:
            if candidate is not None:
                await candidate.stop()
            raise HTTPException(
                status_code=502,
                detail=f"Could not start Ring stream: {exc}",
            ) from exc


@router.get("/frame")
async def get_frame():
    if _stream is None:
        raise HTTPException(
            status_code=409,
            detail="Start the stream first using POST /streams/start",
        )

    frame = await _stream.get_latest_frame()

    if frame is None:
        raise HTTPException(
            status_code=503,
            detail="Waiting for the first video frame",
        )

    success, encoded = cv2.imencode(".jpg", frame)

    if not success:
        raise HTTPException(status_code=500, detail="JPEG encoding failed")

    return Response(content=encoded.tobytes(), media_type="image/jpeg")


@router.get("/live")
async def live_stream():
    """Continuous MJPEG stream viewable directly in any browser (Safari, Chrome, Firefox)."""
    if _stream is None:
        raise HTTPException(
            status_code=409,
            detail="Start the stream first using POST /streams/start",
        )

    async def frame_generator():
        while _stream is not None:
            frame = await _stream.get_latest_frame()
            if frame is not None:
                success, encoded = cv2.imencode(".jpg", frame)
                if success:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + encoded.tobytes()
                        + b"\r\n"
                    )
            await asyncio.sleep(0.04)  # ~25 FPS

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/preview")
async def stream_preview():
    """Web preview page with embedded continuous video feed."""
    from fastapi.responses import HTMLResponse

    html = """<!DOCTYPE html>
<html>
<head>
    <title>Ring Camera Live Feed</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; margin: 0; padding: 2rem; }
        .card { max-width: 820px; margin: 0 auto; background: #1e293b; padding: 1.5rem; border-radius: 14px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        h1 { margin-top: 0; font-size: 1.5rem; color: #38bdf8; }
        img { width: 100%; max-height: 480px; object-fit: contain; border-radius: 8px; border: 2px solid #334155; background: #000; }
        .controls { margin-top: 1.2rem; display: flex; justify-content: center; gap: 1rem; }
        button { padding: 0.6rem 1.4rem; font-size: 0.95rem; font-weight: 600; border-radius: 8px; border: none; cursor: pointer; transition: all 0.2s; }
        .btn-stop { background: #ef4444; color: white; }
        .btn-stop:hover { background: #dc2626; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🎥 Ring Live Camera Feed</h1>
        <img src="/api/v1/streams/live" alt="Ring Live Stream (Loading...)" />
        <div class="controls">
            <button class="btn-stop" onclick="fetch('/api/v1/streams/stop', {method:'POST'}).then(() => alert('Stream stopped.'));">Stop Stream</button>
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/stop")
async def stop_stream():
    global _stream

    async with _stream_lock:
        if _stream is None:
            return {"status": "already_stopped"}

        stream = _stream
        _stream = None
        await stream.stop()

    return {"status": "stopped"}
