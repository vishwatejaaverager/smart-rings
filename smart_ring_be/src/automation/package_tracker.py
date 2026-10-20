
from typing import Any
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END


# A detection must contain a stable tracking ID and confidence.
# These are sample detections for testing, not actual CV output.


class Package(TypedDict):
    status: str
    missing_count: int


class TrackerState(TypedDict):
    observations: list[dict[str, Any]]
    packages: dict[str, Package]
    events: list[dict[str, Any]]
    missing_threshold: int


def process_observations(state: TrackerState) -> dict:
    packages = {
        package_id: dict(package)
        for package_id, package in state["packages"].items()
    }

    events = []
    visible_ids = set()

    # Ignore weak detections.
    for detection in state["observations"]:
        if detection.get("confidence", 0) < 0.60:
            continue

        package_id = detection.get("package_id")
        if not package_id:
            continue

        visible_ids.add(package_id)

        # A newly tracked package has arrived.
        if package_id not in packages:
            packages[package_id] = {
                "status": "present",
                "missing_count": 0,
            }
            events.append({
                "type": "package_arrived",
                "package_id": package_id,
            })
        else:
            package = packages[package_id]

            # A previously missing package is visible again.
            if package["status"] == "missing":
                package["status"] = "present"
                events.append({
                    "type": "package_visible_again",
                    "package_id": package_id,
                })

            package["missing_count"] = 0

    # Only consider packages that were already tracked.
    for package_id, package in packages.items():
        if package_id in visible_ids:
            continue

        if package["status"] == "removed":
            continue

        package["missing_count"] += 1

        if package["missing_count"] >= state["missing_threshold"]:
            package["status"] = "removed"
            events.append({
                "type": "package_removed",
                "package_id": package_id,
            })
        else:
            package["status"] = "missing"

    return {
        "packages": packages,
        "events": events,
    }


def build_graph():
    graph = StateGraph(TrackerState)

    graph.add_node("process_observations", process_observations)
    graph.add_edge(START, "process_observations")
    graph.add_edge("process_observations", END)

    return graph.compile()


package_graph = build_graph()


class PackageTracker:
    def __init__(self, missing_threshold: int = 3):
        self.state: TrackerState = {
            "observations": [],
            "packages": {},
            "events": [],
            "missing_threshold": missing_threshold,
        }

    def observe(self, detections: list[dict[str, Any]]) -> dict:
        result = package_graph.invoke({
            **self.state,
            "observations": detections,
            "events": [],
        })

        self.state = result

        return {
            "packages": result["packages"],
            "events": result["events"],
        }


if __name__ == "__main__":
    tracker = PackageTracker(missing_threshold=3)

    test_frames = [
        # Package arrives.
        [{"package_id": "package-1", "confidence": 0.96}],

        # Still visible.
        [{"package_id": "package-1", "confidence": 0.92}],

        # Temporarily occluded: do not alert yet.
        [],

        # Visible again: reset missing count.
        [{"package_id": "package-1", "confidence": 0.95}],

        # Three consecutive observations without the package.
        [],
        [],
        [],
    ]

    for frame_number, detections in enumerate(test_frames, start=1):
        result = tracker.observe(detections)

        print(f"\nFrame {frame_number}")
        print("Packages:", result["packages"])
        print("Events:", result["events"])
