"""One backend client for typed and transcribed dashboard commands."""
import os
import httpx


class DashboardClient:
    def __init__(self, base_url=None):
        self.base_url = (base_url or os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")

    def request(self, method, path, **kwargs):
        timeout = kwargs.pop("timeout", 8)
        try:
            response = httpx.request(method, self.base_url + path, timeout=timeout, **kwargs)
        except httpx.RequestError as exc:
            raise RuntimeError("Your home server is unavailable. Please check the connection and try again.") from exc
        if not response.is_success:
            try:
                detail = response.json().get("detail", "Please try again.")
            except (ValueError, AttributeError):
                detail = "Please try again."
            raise RuntimeError(detail if isinstance(detail, str) else "The request could not be completed.")
        return response.json()

    def create_rule(self, prompt):
        return self.request("POST", "/api/v1/automations/rules", json={"prompt": prompt}, timeout=65)

    def transcribe(self, audio):
        return self.request("POST", "/api/v1/voice/transcribe", content=audio,
                            headers={"Content-Type": "audio/wav"}, timeout=180)
