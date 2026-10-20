import os
from typing import Any, List, Optional
from inference_sdk import InferenceHTTPClient, InferenceConfiguration
from dotenv import load_dotenv

load_dotenv()

WORKFLOW_ID = "general-segmentation-api-3"
WORKSPACE_NAME = "workgmail"

# The workflow's `classes` value is a text prompt. On this footage "package" returns
# nothing, while "cardboard box" detects each box (1 box -> 1, 2 touching boxes -> 2).
DEFAULT_CLASSES = "cardboard box, package"


class PackageDetection:
    """Standardized representation of a detected package."""

    def __init__(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        confidence: float,
        class_name: str = "package",
        mask: Optional[Any] = None,
        track_id: Optional[int] = None,
    ):
        self.x = float(x)
        self.y = float(y)
        self.width = float(width)
        self.height = float(height)
        self.confidence = float(confidence)
        self.class_name = class_name
        self.mask = mask
        self.track_id = track_id

    @property
    def box_xyxy(self) -> tuple[int, int, int, int]:
        """Returns (x1, y1, x2, y2) pixel bounding coordinates."""
        x1 = int(self.x - self.width / 2)
        y1 = int(self.y - self.height / 2)
        x2 = int(self.x + self.width / 2)
        y2 = int(self.y + self.height / 2)
        return (x1, y1, x2, y2)

    def __repr__(self) -> str:
        return (
            f"PackageDetection(class={self.class_name}, conf={self.confidence:.2f}, "
            f"box=({self.x:.0f}, {self.y:.0f}, {self.width:.0f}, {self.height:.0f}))"
        )


def get_workflow_client() -> InferenceHTTPClient:
    api_key = os.getenv("ROBOFLOW_API_KEY")
    if not api_key:
        raise RuntimeError("ROBOFLOW_API_KEY missing from .env")
    return InferenceHTTPClient(
        api_url="https://serverless.roboflow.com",
        api_key=api_key,
    ).configure(InferenceConfiguration(api_key_transport="header"))


def detect_packages_workflow(
    frame: Any,
    client: Optional[InferenceHTTPClient] = None,
    classes: str = DEFAULT_CLASSES,
    confidence: float = 0.50,
) -> List[PackageDetection]:
    """
    Run the Roboflow general-segmentation-api-3 workflow on an image/frame.
    Returns a list of standardized PackageDetection objects.
    """
    if client is None:
        client = get_workflow_client()

    result = client.run_workflow(
        workspace_name=WORKSPACE_NAME,
        workflow_id=WORKFLOW_ID,
        images={"image": frame},
        parameters={"classes": classes},
        use_cache=True,
    )

    detections: List[PackageDetection] = []

    if result and isinstance(result, list):
        predictions_data = result[0].get("predictions", {})
        raw_items = (
            predictions_data.get("predictions", [])
            if isinstance(predictions_data, dict)
            else predictions_data
        )

        for item in raw_items:
            conf = float(item.get("confidence", 0.0))
            if conf >= confidence:
                detections.append(
                    PackageDetection(
                        x=item.get("x", 0.0),
                        y=item.get("y", 0.0),
                        width=item.get("width", 0.0),
                        height=item.get("height", 0.0),
                        confidence=conf,
                        class_name=item.get("class", "package"),
                        mask=item.get("rle_mask"),
                    )
                )

    return detections


def _is_ultralytics_yolo(model: Any) -> bool:
    """
    Detect an Ultralytics YOLO model.

    Do NOT rely on `hasattr(model, "predict")`: since `inference` 1.8.x the
    Roboflow `InferenceModelsObjectDetectionAdapter` also exposes a `.predict`
    attribute, which made the old duck-type check route Roboflow models down
    the Ultralytics branch and crash with
    "TypeError: 'InferenceModelsObjectDetectionAdapter' object is not callable".

    An Ultralytics YOLO is callable and exposes a `names` mapping; the Roboflow
    adapter is not callable.
    """
    if model is None:
        return False
    if type(model).__name__ == "YOLO":
        return True
    return callable(model) and hasattr(model, "names")


def detect_packages(
    frame: Any,
    model: Optional[Any] = None,
    confidence: float = 0.50,
    iou_threshold: Optional[float] = None,
) -> List[PackageDetection]:
    """
    Unified package detector.
    If `model` is provided, runs local model inference; otherwise calls the Roboflow workflow.
    `iou_threshold` controls Non-Maximum Suppression (NMS) overlap threshold.
    """
    if model is not None:
        # Check if model is an Ultralytics YOLO model
        if _is_ultralytics_yolo(model):
            kwargs = {"conf": confidence, "verbose": False}
            if iou_threshold is not None:
                kwargs["iou"] = iou_threshold
            res = model(frame, **kwargs)[0]
            detections = []
            for b in res.boxes:
                cls_id = int(b.cls[0])
                cls_name = model.names.get(cls_id, "package")
                xywh = b.xywh[0].tolist()
                detections.append(
                    PackageDetection(
                        x=xywh[0],
                        y=xywh[1],
                        width=xywh[2],
                        height=xywh[3],
                        confidence=float(b.conf[0]),
                        class_name=cls_name,
                    )
                )
            return detections

        # Otherwise standard Roboflow inference model
        infer_kwargs = {"confidence": confidence}
        if iou_threshold is not None:
            infer_kwargs["iou_threshold"] = iou_threshold
        result = model.infer(frame, **infer_kwargs)[0]
        raw_predictions = getattr(result, "predictions", [])
        return [
            PackageDetection(
                x=p.x,
                y=p.y,
                width=p.width,
                height=p.height,
                confidence=p.confidence,
                class_name=getattr(p, "class_name", "package"),
            )
            for p in raw_predictions
            if getattr(p, "confidence", 1.0) >= confidence
        ]

    return detect_packages_workflow(frame, confidence=confidence)
