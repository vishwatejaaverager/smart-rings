"""Associate detector boxes across frames without running a second detector."""
from types import SimpleNamespace

import numpy as np


class ObjectTracker:
    def __init__(self):
        self._trackers = {}

    def reset(self):
        self._trackers.clear()

    def update(self, detections, frame):
        # Independent trackers prevent association between different object classes.
        from ultralytics.engine.results import Boxes
        from ultralytics.trackers.bot_sort import BOTSORT

        for source in ("person", "car"):
            objects = [obj for obj in detections if obj.class_name == source]
            for obj in objects:
                obj.track_id = None
            if source not in self._trackers:
                if not objects:
                    continue
                self._trackers[source] = BOTSORT(SimpleNamespace(
                    track_high_thresh=0.25, track_low_thresh=0.1,
                    new_track_thresh=0.25, track_buffer=60, match_thresh=0.8,
                    fuse_score=True, gmc_method="none", with_reid=False,
                    proximity_thresh=0.5, appearance_thresh=0.8, model="auto",
                ))
            rows = [[*obj.box_xyxy, obj.confidence, 0] for obj in objects]
            boxes = Boxes(np.asarray(rows, dtype=np.float32).reshape(-1, 6), frame.shape[:2])
            tracks = self._trackers[source].update(boxes, frame)
            # BoT-SORT output ends with the index into the supplied detections.
            for track in tracks:
                index = int(track[-1])
                if 0 <= index < len(objects):
                    objects[index].track_id = int(track[4])
        return detections
