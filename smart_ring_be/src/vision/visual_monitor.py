"""Periodic visual checks and activity episodes for independently tracked objects."""
import logging
import os
import threading
import time
from dataclasses import dataclass

from src.vision.tracking import ObjectTracker

logger = logging.getLogger(__name__)


@dataclass
class TrackRuleState:
    signature: str
    last_seen: float
    last_checked: float = float("-inf")
    pending: bool = False
    matched: bool | None = None
    episode_alerted: bool = False
    last_fired: float = float("-inf")


class VisualRuleMonitor:
    def __init__(self, reasoner, *, tracker=None, interval=None, grace=5.0, clock=time.monotonic):
        self.reasoner = reasoner
        self.tracker = tracker if tracker is not None else ObjectTracker()
        self.interval = max(0.1, float(interval if interval is not None else os.getenv("VISUAL_RECHECK_SECONDS", "2")))
        self.grace = grace
        self.clock = clock
        self._lock = threading.RLock()
        self._states = {}
        self._seen = {}
        self._once_fired = set()
        self._generation = 0

    def reset(self):
        """Invalidate late callbacks and reset track identities at rewind/shutdown."""
        with self._lock:
            self._generation += 1
            self._states.clear()
            self._seen.clear()
            self.tracker.reset()
        # once_ever belongs to the rule lifecycle, not video playback.

    def update(self, frame, detections, rules, video_time, on_alert, on_detection=None):
        self.tracker.update(detections, frame)
        now = self.clock()
        active = {r.id: r for r in rules if r.source in ("person", "car") and r.vlm_query}
        signatures = {key: rule.model_dump_json() for key, rule in active.items()}
        candidates = []
        with self._lock:
            self._once_fired.intersection_update((key, value) for key, value in signatures.items())
            for obj in detections:
                if obj.class_name not in ("person", "car") or obj.track_id is None:
                    continue
                track_key = (obj.class_name, obj.track_id)
                if track_key not in self._seen and on_detection is not None:
                    on_detection({"source": obj.class_name, "track_id": obj.track_id, "video_time": video_time})
                self._seen[track_key] = now
                x1, y1, x2, y2 = obj.box_xyxy
                h, w = frame.shape[:2]
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                eligible_crop = x2 - x1 > 40 and y2 - y1 > 40
                for rule in active.values():
                    if rule.source != obj.class_name:
                        continue
                    key = (obj.class_name, obj.track_id, rule.id)
                    state = self._states.get(key)
                    if state is None or state.signature != signatures[rule.id]:
                        state = TrackRuleState(signatures[rule.id], now)
                        self._states[key] = state
                    state.last_seen = now
                    if eligible_crop and not state.pending and now - state.last_checked >= self.interval:
                        candidates.append((state.last_checked, key, state, rule, (x1, y1, x2, y2)))
            for key, state in list(self._states.items()):
                if signatures.get(key[2]) != state.signature or now - state.last_seen > self.grace:
                    del self._states[key]
            self._seen = {key: seen for key, seen in self._seen.items() if now - seen <= self.grace}
            # New/least-recently checked people go first; do not repeatedly favour
            # the first detection in the frame. Only current crops are submitted.
            candidates.sort(key=lambda item: (item[0], item[1]))
            for _, key, state, rule, box in candidates:
                if self._states.get(key) is not state:
                    continue
                generation = self._generation
                state.pending = True
                previous_check = state.last_checked
                state.last_checked = now

                def completed(query_key, matched, raw, *, key=key, state=state, rule=rule,
                              generation=generation, video_time=video_time):
                    self._complete(key, state, rule, generation, video_time, matched, raw, on_alert)

                x1, y1, x2, y2 = box
                accepted = self.reasoner.query_boolean_async(
                    frame[y1:y2, x1:x2].copy(), rule.vlm_query,
                    f"{generation}:{key[0]}:{key[1]}:{rule.id}", callback=completed,
                )
                if accepted:
                    break
                state.pending = False
                state.last_checked = previous_check
                # A busy worker will accept none of the other candidates either.
                break

    def _complete(self, key, state, rule, generation, video_time, matched, raw, on_alert):
        event = None
        with self._lock:
            now = self.clock()
            if (generation != self._generation or self._states.get(key) is not state
                    or now - state.last_seen > self.grace):
                return
            state.pending = False
            state.last_checked = now
            if matched is None:
                logger.warning("Visual check unknown: track=%s rule=%s", key[1], rule.id)
                return  # An inference error must not re-arm an already matched episode.
            state.matched = matched
            if not matched:
                state.episode_alerted = False
                return
            once_key = (rule.id, state.signature)
            if (state.episode_alerted or now - state.last_fired < rule.alert.cooldown_seconds
                    or (rule.alert.fire == "once_ever" and once_key in self._once_fired)):
                return
            state.episode_alerted = True
            state.last_fired = now
            if rule.alert.fire == "once_ever":
                self._once_fired.add(once_key)
            event = {
                "rule_id": rule.id, "track_id": key[1], "source": key[0],
                "video_time": video_time, "response": raw,
                "alert": f"🚨 Visual alert for {key[0]} #{key[1]}: "
                         + rule.vlm_query.replace("Answer YES or NO.", "").strip(),
            }
        if event is not None:
            on_alert(event)
