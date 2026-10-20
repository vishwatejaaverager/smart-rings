import os
from pathlib import Path
from ultralytics import YOLO

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_YAML = BASE_DIR / "datasets" / "unified" / "data.yaml"
OUTPUT_DIR = BASE_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def train():
    print("🚀 Starting Unified YOLOv8 Training...")
    print(f"📄 Data YAML: {DATA_YAML}")

    # Load pre-trained nano YOLOv8
    model = YOLO("yolov8n.pt")

    # Train on Apple Silicon GPU (mps) or CPU
    results = model.train(
        data=str(DATA_YAML),
        epochs=15,
        imgsz=640,
        batch=16,
        device="mps",  # Apple Metal GPU acceleration
        project=str(OUTPUT_DIR),
        name="unified_detector",
        exist_ok=True,
    )

    print("✅ Training complete!")
    best_weights = OUTPUT_DIR / "unified_detector" / "weights" / "best.pt"
    print(f"🎯 Best model saved at: {best_weights}")


if __name__ == "__main__":
    train()
