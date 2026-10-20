
import asyncio
import os
from typing import Optional
from urllib.parse import urljoin

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from dotenv import load_dotenv

from src.integrations.ring.client import RingAPIError, RingClient

load_dotenv()


class RingVideoStream:
    def __init__(
        self,
        device_id: Optional[str] = None,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("RING_API_BASE_URL", "https://api.amazonvision.com/v1")
        ).rstrip("/")
        self.token = token or os.getenv("RING_ACCESS_TOKEN")
        self.device_id = device_id or os.getenv("RING_DEVICE_ID")

        if not self.token:
            raise ValueError("RING_ACCESS_TOKEN is missing from .env")

        self.pc: Optional[RTCPeerConnection] = None
        self.session_url: Optional[str] = None
        self.latest_frame = None
        self._frame_lock = asyncio.Lock()
        self._receiver_task: Optional[asyncio.Task] = None

    async def discover_device_id(self) -> str:
        """Query GET /devices via RingClient and select data[0]['id']."""
        client = RingClient(access_token=self.token, base_url=self.base_url)
        try:
            resp = await client.get_devices()
            devices = resp.get("data", []) if isinstance(resp, dict) else []
            if not devices or not isinstance(devices, list):
                raise ValueError("No devices found on the Ring account")
            device_id = devices[0].get("id")
            if not device_id:
                raise ValueError("Discovered device is missing an 'id' field")
            return device_id
        finally:
            await client.close()

    async def start(self):
        if not self.device_id:
            self.device_id = await self.discover_device_id()

        self.pc = RTCPeerConnection()

        @self.pc.on("track")
        def on_track(track):
            if track.kind == "video":
                self._receiver_task = asyncio.create_task(
                    self._receive_frames(track)
                )

        # Receive video only.
        self.pc.addTransceiver("video", direction="recvonly")

        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)

        whep_endpoint = (
            f"{self.base_url}/devices/{self.device_id}/media/streaming/whep/sessions"
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    whep_endpoint,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/sdp",
                        "Accept": "application/sdp",
                    },
                    content=self.pc.localDescription.sdp,
                )

                if response.status_code != 201:
                    error_text = response.text[:300]
                    if response.status_code == 401:
                        raise RingAPIError(
                            f"Ring API HTTP 401 Unauthorized: Invalid or expired access token ({error_text})",
                            status_code=401,
                        )
                    raise RingAPIError(
                        f"WHEP session creation failed (HTTP {response.status_code}): {error_text}",
                        status_code=response.status_code,
                    )

                self.session_url = response.headers.get("Location")
                if self.session_url and not self.session_url.startswith("http"):
                    self.session_url = urljoin(self.base_url, self.session_url)

                answer_sdp = response.text

            await self.pc.setRemoteDescription(
                RTCSessionDescription(
                    sdp=answer_sdp,
                    type="answer",
                )
            )
        except Exception:
            await self.stop()
            raise

        return {
            "status": "connecting",
            "device_id": self.device_id,
            "session_url": self.session_url,
        }

    async def _receive_frames(self, track):
        while True:
            try:
                frame = await track.recv()
                image = frame.to_ndarray(format="bgr24")

                async with self._frame_lock:
                    self.latest_frame = image

            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"Ring video receiver stopped: {exc}")
                break

    async def get_latest_frame(self):
        async with self._frame_lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    async def stop(self):
        if self._receiver_task:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None

        if self.session_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.delete(
                        self.session_url,
                        headers={"Authorization": f"Bearer {self.token}"},
                    )
            except Exception as exc:
                print(f"Warning: Failed to release Ring WHEP session: {exc}")
            finally:
                self.session_url = None

        if self.pc:
            await self.pc.close()
            self.pc = None
