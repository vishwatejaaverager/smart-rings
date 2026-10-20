"""Download the compact English Whisper model into the project, once."""
from pathlib import Path
from huggingface_hub import snapshot_download

if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "models" / "whisper-base.en"
    snapshot_download("Systran/faster-whisper-base.en", local_dir=str(destination))
    print(f"Voice model ready at {destination}")
