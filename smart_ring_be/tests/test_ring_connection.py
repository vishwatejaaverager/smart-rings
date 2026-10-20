
import asyncio
import json
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.integrations.ring.client import RingAPIError, RingClient


async def main():
    try:
        ring = RingClient()
    except Exception as exc:
        print(f"❌ Failed to initialize RingClient: {exc}")
        return

    try:
        devices = await ring.get_devices()
        print("✅ Successfully connected to Ring API!")
        print("Ring devices:")
        print(json.dumps(devices, indent=2))
    except RingAPIError as exc:
        if exc.status_code == 401:
            print(f"⚠️  Ring API Authentication Failed (HTTP 401 Unauthorized)")
            print(f"   Detail: {exc}")
            print("   Note: The RING_ACCESS_TOKEN in .env appears to be expired or invalid.")
            print("   Please generate a fresh token from Ring Developer Portal and update .env.")
        else:
            print(f"❌ Ring API error (HTTP {exc.status_code}): {exc}")
    except Exception as exc:
        print(f"❌ Unexpected error connecting to Ring: {exc}")
    finally:
        await ring.close()


if __name__ == "__main__":
    asyncio.run(main())
