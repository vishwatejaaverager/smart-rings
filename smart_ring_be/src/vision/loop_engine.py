import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import cv2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inference import get_model

from src.app import (
    CONFIDENCE,
    IOU_THRESHOLD,
    MODEL_ID,
    VerifiedPackageCounter,
    WorkflowVerifier,
)
from src.evaluator.engine import evaluate
from src.evaluator.models import Rule, RuleState, StableObservation
from src.notifications.webhook import send_alert_webhook
from src.storage.rules_store import rules_store
from src.storage.timeline_store import timeline_store
from src.vision.package_detection import detect_packages
from src.vision.vlm_reasoner import vlm_reasoner
from src.vision.visual_monitor import VisualRuleMonitor

load_dotenv()

LOCAL_YOLO_PATH = ROOT / "models" / "unified_detector" / "weights" / "best.pt"
DEFAULT_VIDEO_PATH = ROOT / "vids" / "theft.mp4"


class ContinuousVideoLoopEngine:
    """
    Continuous video monitoring engine that loops video in the background,
    runs fast local YOLO + Roboflow Cloud Workflow verification, and
    dynamically evaluates active rules created via Alexa / MCP.
    """

    def __init__(self, video_path: Path = DEFAULT_VIDEO_PATH):
        self.video_path = Path(video_path)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.visual_monitor = VisualRuleMonitor(vlm_reasoner)

        # Telemetry
        self.is_running = False
        self.loop_cycle = 0
        self.current_frame = 0
        self.total_frames = 0
        self.fps = 30.0
        self.last_status = "Initialized"
        self.last_alert: Optional[Dict[str, Any]] = None
        self.alerts_fired_count = 0

    def start(self) -> bool:
        """Starts the looping engine in a background daemon thread."""
        with self._lock:
            if self.is_running:
                print("ℹ️ [Loop Engine] Already running.")
                return False

            if not self.video_path.exists():
                print(f"❌ [Loop Engine] Video file not found: {self.video_path}")
                return False

            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ContinuousVideoLoopEngine")
            self._thread.start()
            self.is_running = True
            print(f"▶️ [Loop Engine] Started continuous background monitoring on '{self.video_path.name}'")
            return True

    def stop(self) -> None:
        """Stops the looping engine cleanly."""
        with self._lock:
            if not self.is_running:
                return
            print("⏹️ [Loop Engine] Stopping continuous background monitoring...")
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=4.0)

        with self._lock:
            self.is_running = False
            self.last_status = "Stopped"
            print("🛑 [Loop Engine] Stopped.")

    def get_status(self) -> Dict[str, Any]:
        """Returns engine runtime status and telemetry."""
        with self._lock:
            active_rules = rules_store.get_active_rules()
            return {
                "is_running": self.is_running,
                "video_file": self.video_path.name,
                "loop_cycle": self.loop_cycle,
                "current_frame": self.current_frame,
                "total_frames": self.total_frames,
                "fps": self.fps,
                "active_rules_count": len(active_rules),
                "active_rules": [r.id for r in active_rules],
                "last_status": self.last_status,
                "alerts_fired_count": self.alerts_fired_count,
                "last_alert": self.last_alert,
            }

    def _run_loop(self) -> None:
        """Main background loop."""
        model = None
        if LOCAL_YOLO_PATH.exists():
            try:
                from ultralytics import YOLO
                print(f"📦 [Loop Engine] Loading Unified Detector: {LOCAL_YOLO_PATH.name}")
                model = YOLO(str(LOCAL_YOLO_PATH))
            except Exception as e:
                print(f"⚠️ [Loop Engine] Failed to load unified YOLO: {e}, falling back to Roboflow")

        if model is None:
            print(f"📹 [Loop Engine] Loading local detector ({MODEL_ID})...")
            try:
                model = get_model(model_id=MODEL_ID, api_key=os.getenv("ROBOFLOW_API_KEY"))
            except Exception as exc:
                print(f"❌ [Loop Engine] Failed to load local detector: {exc}")
                with self._lock:
                    self.is_running = False
                    self.last_status = f"Model load error: {exc}"
                return

        verifier = WorkflowVerifier()
        counter = VerifiedPackageCounter(verifier, enable_default_alert=False)

        try:
            self._loop_cycles(model, verifier, counter)
        except Exception as exc:
            # Without this the thread dies silently while /automations/status
            # keeps reporting is_running=True and "Running Cycle #N".
            print(f"❌ [Loop Engine] Fatal error in background loop: {exc}")
            import traceback

            traceback.print_exc()
            with self._lock:
                self.is_running = False
                self.last_status = f"Engine crashed: {exc}"
        else:
            with self._lock:
                self.is_running = False
                if self.last_status != "Stopped":
                    self.last_status = "Stopped"
        finally:
            self.visual_monitor.reset()
            verifier.stop()
            with self._lock:
                self.is_running = False

    def _loop_cycles(self, model, verifier, counter) -> None:
        """Cycle the video forever, evaluating rules on verified changes."""
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(str(self.video_path))
            if not cap.isOpened():
                print(f"❌ [Loop Engine] Cannot open video: {self.video_path}")
                break

            self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            frame_delay = 1.0 / self.fps

            with self._lock:
                self.loop_cycle += 1
                self.last_status = f"Running Cycle #{self.loop_cycle}"
                print(f"\n🔄 [Loop Engine] Starting Loop Cycle #{self.loop_cycle} ({self.total_frames} frames @ {self.fps:.1f} FPS)")

            # Clean state for new video cycle
            self.visual_monitor.reset()
            counter.reset_for_new_cycle()
            stable_history: List[StableObservation] = []
            rule_states: Dict[str, RuleState] = {}
            fired_this_cycle: Set[str] = set()
            frame_idx = 0

            try:
                while not self._stop_event.is_set():
                    started = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        # End of video reached; break inner loop to rewind
                        break

                    video_time = frame_idx / self.fps
                    frame_idx += 1
                    with self._lock:
                        self.current_frame = frame_idx

                    # 1. Fast local package & object detection on current frame
                    raw_detections = detect_packages(
                        frame,
                        model=model,
                        confidence=CONFIDENCE,
                        iou_threshold=IOU_THRESHOLD,
                    )
                    packages = [d for d in raw_detections if d.class_name == "package"]
                    raw_count = len(packages)

                    # 2. Hybrid Verification Step
                    counter.update(raw_count, frame, video_time=round(video_time, 3))

                    # 3. Visual reasoning (Person & Car) + Visual Rules
                    active_rules = rules_store.get_active_rules()
                    def _on_visual_alert(event, cycle=self.loop_cycle):
                        if self._stop_event.is_set() or not any(
                            r.id == event["rule_id"] for r in rules_store.get_active_rules()
                        ):
                            return
                        payload = {
                            "event": "RULE_ALERT_FIRED", "timestamp": time.time(),
                            **event, "value": 1, "cycle": cycle,
                        }
                        with self._lock:
                            self.last_alert = payload
                            self.alerts_fired_count += 1
                        timeline_store.log_event("visual_rule_alert", event)
                        print(f"🚨 [LOOP VISUAL ALERT] track={event['track_id']} rule={event['rule_id']}")
                        send_alert_webhook(payload=payload, frame=None, sync=False)

                    self.visual_monitor.update(
                        frame, raw_detections, active_rules, video_time, _on_visual_alert,
                        on_detection=lambda event: timeline_store.log_event(f"{event['source']}_detected", event),
                    )

                    # 4. Periodic Deadline Check (every 150 frames ~ 5 seconds)
                    if frame_idx % 150 == 0:
                        def _deadline_alert_cb(msg: str):
                            print(f"⏰ [DEADLINE MISSED ALERT] {msg}")
                            send_alert_webhook(
                                payload={
                                    "event": "DEADLINE_MISSED",
                                    "timestamp": time.time(),
                                    "alert": f"⏰ {msg}",
                                },
                                frame=None,
                                sync=False,
                            )
                        timeline_store.check_deadlines(_deadline_alert_cb)

                    # 5. Dynamic Package Rule Evaluator (evaluate on workflow-verified changes)
                    new_verified = counter.poll_verified_changes()
                    for change in new_verified:
                        obs = StableObservation(
                            timestamp=change["timestamp"],
                            package_count=change["package_count"],
                        )
                        stable_history.append(obs)
                        timeline_store.log_event("package_count_change", {
                            "package_count": change["package_count"],
                            "video_time": change["timestamp"],
                        })

                        pkg_rules = [r for r in active_rules if r.source == "package_count" and r.derive is not None]
                        for rule in pkg_rules:
                            if rule.id not in rule_states:
                                rule_states[rule.id] = RuleState()

                            decision, new_state = evaluate(
                                rule, stable_history, rule_states[rule.id]
                            )
                            rule_states[rule.id] = new_state

                            if decision.fired and rule.id not in fired_this_cycle:
                                fired_this_cycle.add(rule.id)
                                alert_text = decision.alert_text or f"Condition met for rule '{rule.id}'"
                                if not alert_text.startswith("🚨"):
                                    alert_text = f"🚨 {alert_text}"

                                print(
                                    f"🚨 [LOOP ALERT] Cycle #{self.loop_cycle} | t={decision.timestamp:5.2f}s | "
                                    f"[{rule.id}] {alert_text}"
                                )

                                alert_payload = {
                                    "event": "RULE_ALERT_FIRED",
                                    "timestamp": time.time(),
                                    "video_time": decision.timestamp,
                                    "rule_id": rule.id,
                                    "alert": alert_text,
                                    "value": decision.value,
                                    "cycle": self.loop_cycle,
                                }

                                with self._lock:
                                    self.last_alert = alert_payload
                                    self.alerts_fired_count += 1

                                timeline_store.log_event("rule_alert", {
                                    "rule_id": rule.id,
                                    "alert": alert_text,
                                    "video_time": decision.timestamp,
                                    "value": decision.value,
                                })

                                send_alert_webhook(
                                    payload=alert_payload,
                                    frame=None,
                                    sync=True,
                                )

                    # Pacing to match real-time video playback rate
                    elapsed = time.monotonic() - started
                    if frame_delay > elapsed:
                        time.sleep(frame_delay - elapsed)

            finally:
                cap.release()

            # End of video: allow in-flight verification to finish before restarting cycle
            if not self._stop_event.is_set():
                counter.finish()
                # Check if any verified change occurred on the final frames
                for change in counter.poll_verified_changes():
                    obs = StableObservation(
                        timestamp=change["timestamp"],
                        package_count=change["package_count"],
                    )
                    stable_history.append(obs)
                    pkg_rules = [r for r in rules_store.get_active_rules() if r.source == "package_count" and r.derive is not None]
                    for rule in pkg_rules:
                        if rule.id not in rule_states:
                            rule_states[rule.id] = RuleState()
                        decision, new_state = evaluate(rule, stable_history, rule_states[rule.id])
                        rule_states[rule.id] = new_state
                        if decision.fired and rule.id not in fired_this_cycle:
                            fired_this_cycle.add(rule.id)
                            alert_text = decision.alert_text or f"Condition met for rule '{rule.id}'"
                            if not alert_text.startswith("🚨"):
                                alert_text = f"🚨 {alert_text}"
                            send_alert_webhook(
                                payload={
                                    "event": "RULE_ALERT_FIRED",
                                    "timestamp": time.time(),
                                    "video_time": decision.timestamp,
                                    "rule_id": rule.id,
                                    "alert": alert_text,
                                    "value": decision.value,
                                    "cycle": self.loop_cycle,
                                },
                                frame=None,
                                sync=True,
                            )

                # Clean pause before rewinding back to frame 0
                time.sleep(0.5)


# Global singleton instance for backend
loop_engine = ContinuousVideoLoopEngine()


if __name__ == "__main__":
    print("🚀 [CLI] Starting Standalone Continuous Video Loop Engine...")
    print("Press Ctrl+C to stop.\n")
    loop_engine.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⏹️ [CLI] Interrupted. Stopping engine...")
        loop_engine.stop()
        print("Done.")
