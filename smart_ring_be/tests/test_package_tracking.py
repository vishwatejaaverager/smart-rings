
import os
from pathlib import Path

import cv2
from dotenv import load_dotenv
from inference import get_model

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
VIDEO_PATH = ROOT / "vids" / "theft.mp4"
MODEL_ID = "products-couter-yxfxm/2"
CONFIDENCE = 0.20

api_key = os.getenv("ROBOFLOW_API_KEY")
if not api_key:
    raise RuntimeError("ROBOFLOW_API_KEY missing from .env")

model = get_model(model_id=MODEL_ID, api_key=api_key)


# Function 1: Detect boxes in a single frame
def detect_boxes(frame):
    result = model.infer(
        frame,
        confidence=CONFIDENCE,
    )[0]

    predictions = result.predictions

    if predictions:
        print(f"{len(predictions)} box(es) detected")

    return predictions


# Continuous video input
cap = cv2.VideoCapture(str(VIDEO_PATH))

if not cap.isOpened():
    raise RuntimeError(f"Cannot open video: {VIDEO_PATH}")

frame_number = 0

try:
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame_number += 1

        # Invoke our detection function for every frame
        predictions = detect_boxes(frame)

        # Draw detected boxes
        for p in predictions:
            x1 = int(p.x - p.width / 2)
            y1 = int(p.y - p.height / 2)
            x2 = int(p.x + p.width / 2)
            y2 = int(p.y + p.height / 2)

            cv2.rectangle(
                frame, (x1, y1), (x2, y2), (0, 255, 0), 2
            )

            cv2.putText(
                frame,
                f"{p.class_name} {p.confidence:.2f}",
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

        cv2.putText(
            frame,
            f"Frame: {frame_number} | Boxes: {len(predictions)}",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 255),
            2,
        )

        cv2.imshow("Roboflow Detection", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()
