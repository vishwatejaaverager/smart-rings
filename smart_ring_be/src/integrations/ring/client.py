
import os

# pyrefly: ignore [missing-import]
import httpx
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

load_dotenv()


class RingAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class RingClient:
    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("RING_API_BASE_URL", "https://api.amazonvision.com/v1")
        ).rstrip("/")

        self.access_token = access_token or os.getenv("RING_ACCESS_TOKEN")

        if not self.access_token:
            raise ValueError("RING_ACCESS_TOKEN is missing from .env")

        self.http = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def get_devices(self):
        return await self._get("/devices")

    async def get_device_status(self, device_id: str):
        return await self._get(f"/devices/{device_id}/status")

    async def get_device_capabilities(self, device_id: str):
        return await self._get(f"/devices/{device_id}/capabilities")

    async def get_event_history(self):
        return await self._get("/event_history")

    async def _get(self, path: str):
        try:
            response = await self.http.get(path)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise RingAPIError(
                f"Ring API HTTP {exc.response.status_code}: "
                f"{exc.response.text[:300]}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise RingAPIError(
                f"Ring API connection failed: {exc}"
            ) from exc

    async def close(self):
        await self.http.aclose()
