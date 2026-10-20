import base64
import os
import threading
import time
from typing import Any, Dict, Optional
import cv2
from dotenv import load_dotenv
import httpx
import numpy as np

load_dotenv()


def encode_frame_to_base64(frame: np.ndarray, max_width: int = 640, quality: int = 70) -> Optional[str]:
    """Encode OpenCV BGR frame to base64-encoded JPEG, downscaled for fast TV rendering."""
    try:
        h, w = frame.shape[:2]
        if w > max_width:
            scale = max_width / float(w)
            thumb = cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        else:
            thumb = frame
        ok, buf = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception as exc:
        print(f"⚠️ [Webhook] Frame encoding failed: {exc}")
        return None


def _post_webhook(url: str, payload: Dict[str, Any], secret: Optional[str] = None) -> bool:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Smart-Ring-Package-Monitor/1.0",
    }
    if secret:
        headers["X-Webhook-Secret"] = secret
        headers["Authorization"] = f"Bearer {secret}"

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code in (200, 201, 202, 204):
                print(f"🔔 [Webhook] Successfully dispatched alert to {url} (HTTP {response.status_code}) -> {response.text.strip()}")
                return True
            else:
                print(
                    f"⚠️ [Webhook] Webhook endpoint returned HTTP {response.status_code}: {response.text[:200]}"
                )
                return False
    except Exception as exc:
        print(f"⚠️ [Webhook] Failed to deliver alert to {url}: {exc}")
        return False


def send_alert_webhook(
    payload: Dict[str, Any],
    frame: Optional[np.ndarray] = None,
    sync: bool = False,
    webhook_url: Optional[str] = None,
) -> bool:
    """
    Dispatch alert payload to configured ALERT_WEBHOOK_URL.

    Runs in a background daemon thread by default to avoid blocking the video stream.
    If frame is provided, it is compressed to JPEG and included as base64 in snapshot.
    """
    target_url = webhook_url or os.getenv("ALERT_WEBHOOK_URL")
    if not target_url:
        print("ℹ️ [Webhook] ALERT_WEBHOOK_URL not configured. Alert not sent to remote endpoint.")
        return False

    secret = os.getenv("ALERT_WEBHOOK_SECRET")
    include_frame = os.getenv("INCLUDE_FRAME_IN_WEBHOOK", "true").lower() in ("1", "true", "yes")

    body = dict(payload)
    if "timestamp" not in body:
        body["timestamp"] = time.time()

    if frame is not None and include_frame:
        b64_img = encode_frame_to_base64(frame)
        if b64_img:
            body["snapshot"] = {
                "format": "jpeg",
                "image_base64": b64_img,
            }

    if sync:
        return _post_webhook(target_url, body, secret=secret)

    thread = threading.Thread(
        target=_post_webhook,
        args=(target_url, body, secret),
        daemon=True,
    )
    thread.start()
    return True
