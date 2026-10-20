import base64
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional
import cv2
import httpx

import os

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5vl:3b")


class VLMReasoner:
    """
    Asynchronous Layer-2 Visual Reasoner powered by local Qwen2.5-VL via Ollama.
    Processes crops of cars and people in the background without blocking the video loop.
    """

    def __init__(self, ollama_url: str = OLLAMA_URL, model_name: str = MODEL_NAME):
        self.ollama_url = ollama_url
        self.model_name = model_name
        self.busy = False
        self._lock = threading.Lock()
        
        # Cache results: {object_key: (result_dict, timestamp)}
        self.cache: Dict[str, Dict[str, Any]] = {}

    def _reserve(self):
        with self._lock:
            if self.busy:
                return False
            self.busy = True
            return True

    def _release(self):
        with self._lock:
            self.busy = False

    def _encode_image(self, crop: Any) -> Optional[str]:
        """Encodes an OpenCV BGR image crop to Base64 JPEG."""
        if crop is None or crop.size == 0:
            return None
        # Resize to max 384x384 for fast tokenization
        h, w = crop.shape[:2]
        if max(h, w) > 384:
            scale = 384.0 / max(h, w)
            crop = cv2.resize(crop, (int(w * scale), int(h * scale)))
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None
        return base64.b64encode(buf).decode("utf-8")

    def _query_qwen(self, prompt: str, image_b64: str) -> Optional[Dict[str, Any]]:
        """Sends an inference request to Ollama."""
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    self.ollama_url,
                    json={
                        "model": self.model_name,
                        "prompt": prompt,
                        "images": [image_b64],
                        "format": "json",
                        "stream": False,
                    },
                )
                if resp.status_code == 200:
                    raw_text = resp.json().get("response", "{}")
                    try:
                        return json.loads(raw_text)
                    except json.JSONDecodeError:
                        return {"raw": raw_text}
                else:
                    logger.warning(f"Ollama returned status {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"VLM inference error: {e}")
        return None

    def analyze_crop_async(
        self,
        crop: Any,
        class_name: str,
        object_id: str,
        callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        """
        Asynchronously sends crop to Qwen2.5-VL in a background thread.
        Calls `callback(object_id, attributes)` when analysis completes.
        """
        image_b64 = self._encode_image(crop)
        if not image_b64 or not self._reserve():
            return False

        def _worker():
            try:
                if class_name == "car":
                    prompt = (
                        "Analyze this vehicle crop. Output JSON with keys: "
                        "'color' (dominant vehicle color, e.g. white, silver, black, red, blue), "
                        "'vehicle_type' (sedan, suv, truck, van), "
                        "'license_plate' (license plate text if visible, otherwise null)."
                    )
                elif class_name == "person":
                    prompt = (
                        "Analyze this person crop. Output JSON with keys: "
                        "'upper_color' (dominant shirt/jacket color), "
                        "'clothing' (brief apparel description, e.g. blue hoodie, black jacket), "
                        "'action' (brief action, e.g. walking, carrying box, standing)."
                    )
                else:
                    prompt = "Describe this object concisely in JSON with keys: 'description', 'color'."

                result = self._query_qwen(prompt, image_b64)
                if result:
                    with self._lock:
                        self.cache[object_id] = result
                    if callback:
                        callback(object_id, result)
                    print(f"✨ [Layer 2 Qwen] Analyzed {class_name} ({object_id}): {result}")
            finally:
                self._release()

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def query_boolean_async(
        self,
        crop: Any,
        vlm_query: str,
        object_id: str,
        callback: Optional[Callable[[str, Optional[bool], str], None]] = None,
    ):
        """
        Asynchronously sends crop to Qwen with a targeted YES/NO question.
        Calls callback(object_id, is_yes, raw_response).
        """
        image_b64 = self._encode_image(crop)
        if not image_b64 or not self._reserve():
            return False

        def _worker():
            is_yes, raw = None, ""
            try:
                prompt = (
                    f"{vlm_query}\n"
                    "Instructions: Answer ONLY with the word 'YES' or 'NO'. Do not include punctuation or extra words."
                )
                with httpx.Client(timeout=15.0) as client:
                    resp = client.post(
                        self.ollama_url,
                        json={
                            "model": self.model_name,
                            "prompt": prompt,
                            "images": [image_b64],
                            "stream": False,
                        },
                    )
                    if resp.status_code == 200:
                        raw = resp.json().get("response", "").strip()
                        answer = raw.upper().rstrip(".! ")
                        is_yes = True if answer == "YES" else False if answer == "NO" else None
                        print(f"✨ [Layer 2 Qwen Query] [{object_id}] '{vlm_query}' -> '{raw}' (Match: {is_yes})")
                    else:
                        logger.warning("VLM boolean HTTP %s", resp.status_code)
            except Exception as e:
                logger.error(f"Error querying VLM boolean: {e}")
            finally:
                try:
                    if callback:
                        callback(object_id, is_yes, raw)
                finally:
                    self._release()

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def get_attributes(self, object_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves cached attributes for an object if available."""
        with self._lock:
            return self.cache.get(object_id)


# Global singleton instance
vlm_reasoner = VLMReasoner()
