import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from src.integrations.ring.stream import RingVideoStream


async def main():
    print("=" * 60)
    print("🎥 Ring Live Video Stream Viewer")
    print("=" * 60)

    stream = RingVideoStream()
    print("Connecting to Ring camera via WebRTC (WHEP)...")

    try:
        info = await stream.start()
        print(f"✅ Stream connected!")
        print(f"   Device ID: {info.get('device_id')}")
        print(f"   Session:   {info.get('session_url')[:60]}...")
        print("\nPress 'q' or 'ESC' on the video window to stop.")
    except Exception as exc:
        print(f"❌ Failed to start live stream: {exc}")
        return

    window_name = "Ring Camera Live Feed (Press 'q' to exit)"
    first_frame_shown = False

    try:
        while True:
            frame = await stream.get_latest_frame()

            if frame is not None:
                if not first_frame_shown:
                    print("🟢 Live video frames streaming in real-time (25-30 FPS)!")
                    first_frame_shown = True

                cv2.imshow(window_name, frame)
                key = cv2.waitKey(30) & 0xFF
                if key in (27, ord("q"), ord("Q")):
                    break
            else:
                # Waiting for incoming WebRTC video frames
                await asyncio.sleep(0.05)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[Ctrl+C detected]")
    finally:
        print("Stopping stream and releasing Ring session...")
        await stream.stop()
        cv2.destroyAllWindows()
        print("✅ Session closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
