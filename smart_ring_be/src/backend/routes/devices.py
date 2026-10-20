
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException

from src.integrations.ring.client import RingClient, RingAPIError

router = APIRouter(prefix="/devices", tags=["Ring Devices"])


def _handle_ring_error(exc: Exception):
    if isinstance(exc, RingAPIError) and exc.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Ring access token is expired or invalid. Please update RING_ACCESS_TOKEN in .env.",
        ) from exc
    raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("")
async def list_devices():
    ring = None
    try:
        ring = RingClient()
        return await ring.get_devices()
    except (RingAPIError, ValueError) as exc:
        _handle_ring_error(exc)
    finally:
        if ring:
            await ring.close()


@router.get("/{device_id}/status")
async def device_status(device_id: str):
    ring = None
    try:
        ring = RingClient()
        return await ring.get_device_status(device_id)
    except (RingAPIError, ValueError) as exc:
        _handle_ring_error(exc)
    finally:
        if ring:
            await ring.close()


@router.get("/{device_id}/capabilities")
async def device_capabilities(device_id: str):
    ring = None
    try:
        ring = RingClient()
        return await ring.get_device_capabilities(device_id)
    except (RingAPIError, ValueError) as exc:
        _handle_ring_error(exc)
    finally:
        if ring:
            await ring.close()


@router.get("/events/history")
async def event_history():
    ring = None
    try:
        ring = RingClient()
        return await ring.get_event_history()
    except (RingAPIError, ValueError) as exc:
        _handle_ring_error(exc)
    finally:
        if ring:
            await ring.close()
