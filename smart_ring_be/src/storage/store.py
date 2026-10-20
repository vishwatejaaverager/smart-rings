import json
from pathlib import Path
from typing import List, Union

from src.evaluator.models import Observation

DEFAULT_OBSERVATIONS_PATH = Path(__file__).resolve().parent / "observations.json"


def load_observations(
    path: Union[Path, str] = DEFAULT_OBSERVATIONS_PATH,
) -> List[Observation]:
    """Load stored history observations from storage or return empty list if not present."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    with open(file_path, "r") as f:
        data = json.load(f)
    return [
        Observation(timestamp=item["timestamp"], package_count=item["package_count"])
        for item in data
    ]


def save_observations(
    observations: List[Observation],
    path: Union[Path, str] = DEFAULT_OBSERVATIONS_PATH,
) -> None:
    """Save observations into storage."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(
            [{"timestamp": o.timestamp, "package_count": o.package_count} for o in observations],
            f,
            indent=2,
        )
