import argparse
from enum import Enum
import os
from pathlib import Path
import threading
import time

# Suppress optional inference component warnings
os.environ["CORE_MODEL_GAZE_ENABLED"] = "False"
os.environ["CORE_MODEL_SAM_ENABLED"] = "False"
os.environ["CORE_MODEL_SAM3_ENABLED"] = "False"
os.environ["CORE_MODEL_YOLO_WORLD_ENABLED"] = "False"

import cv2
import socket
import subprocess
import webbrowser
from typing import List, Optional, Set

from dotenv import load_dotenv
from inference import get_model

import sys

# Ensure project root is in sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from src.evaluator.engine import evaluate
from src.evaluator.models import Rule, RuleState, StableObservation
from src.notifications.webhook import send_alert_webhook
from src.storage.rules_store import rules_store
from src.storage.timeline_store import timeline_store
from src.vision.package_detection import detect_packages
from src.vision.vlm_reasoner import vlm_reasoner
from src.vision.visual_monitor import VisualRuleMonitor

load_dotenv()

VIDEO_PATH = ROOT / "vids" / "theft.mp4"

_last_rule_sync = 0.0
_cached_server_rules: List[Rule] = []


def get_live_rules() -> List[Rule]:
    """Retrieves active rules from FastAPI server in memory or local rules_store."""
    global _last_rule_sync, _cached_server_rules
    now = time.monotonic()
    if now - _last_rule_sync > 0.4:
        _last_rule_sync = now
        try:
            with httpx.Client(timeout=0.15) as client:
                resp = client.get("http://127.0.0.1:8000/api/v1/automations/rules")
                if resp.status_code == 200:
                    entries = resp.json().get("rules", [])
                    synced = []
                    for e in entries:
                        if e.get("enabled", True) and "rule" in e:
                            try:
                                synced.append(Rule.model_validate(e["rule"]))
                            except Exception:
                                pass
                    _cached_server_rules = synced
                    return _cached_server_rules
        except Exception:
            pass
    if _cached_server_rules:
        return _cached_server_rules
    return rules_store.get_active_rules()


def clear_all_rules_everywhere():
    """Wipes all active rules from memory in both local store and server."""
    rules_store.clear_rules()
    try:
        with httpx.Client(timeout=0.5) as client:
            client.delete("http://127.0.0.1:8000/api/v1/automations/rules")
    except Exception:
        pass


def is_port_open(port: int) -> bool:
    """Check if local TCP port is currently open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_backend_active():
    """Ensures FastAPI backend is running on port 8000 for webhooks and WebSocket bridge."""
    if is_port_open(8000):
        print("⚡ [Orchestrator] FastAPI backend is active on http://127.0.0.1:8000")
        return None

    print("🚀 [Orchestrator] Launching FastAPI backend on http://127.0.0.1:8000...")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.backend.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _stream_backend_logs():
        for line in iter(proc.stdout.readline, ""):
            line_clean = line.strip()
            if not line_clean:
                continue
            if (
                "webhook" in line_clean.lower()
                or "fire tv" in line_clean.lower()
                or "client connected" in line_clean.lower()
                or "broadcasted alert" in line_clean.lower()
                or "started server" in line_clean.lower()
                or "startup complete" in line_clean.lower()
            ):
                print(f"📡 [FastAPI Server] {line_clean}")

    threading.Thread(target=_stream_backend_logs, daemon=True, name="BackendLogStreamer").start()

    for _ in range(25):
        time.sleep(0.3)
        if is_port_open(8000):
            print("✅ [Orchestrator] FastAPI backend ready (Webhooks & Fire TV WS active)!")
            return proc
    return proc


def ensure_alexa_ui_active():
    """Ensures Alexa Streamlit dashboard is running and opens in the browser."""
    dashboard_url = "http://localhost:8501"
    if is_port_open(8501):
        print(f"⚡ [Orchestrator] Alexa UI is already active. Opening {dashboard_url}...")
        webbrowser.open(dashboard_url)
        return None

    print(f"🚀 [Orchestrator] Launching Alexa UI on {dashboard_url}...")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "src/ui/dashboard.py",
            "--server.port",
            "8501",
            "--server.headless",
            "true",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(25):
        time.sleep(0.3)
        if is_port_open(8501):
            break

    print(f"✅ [Orchestrator] Alexa UI active! Opening {dashboard_url} in your browser...")
    webbrowser.open(dashboard_url)
    return proc


def invoke_firetv_app():
    """Launches the Fire TV app on the connected device/emulator in the background."""
    def _launch():
        firetv_dir = ROOT.parent / "firetv-app"
        vega_bin = Path("/Users/admin/vega/bin/vega")
        if not vega_bin.exists():
            vega_bin = Path("vega")
        try:
            print("📺 [Fire TV] Invoking SeniorCareTV app on Fire TV...")
            subprocess.run(
                [str(vega_bin), "device", "launch-app", "--dir", str(firetv_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            print("✅ [Fire TV] SeniorCareTV launched!")
        except Exception as exc:
            print(f"ℹ️ [Fire TV] Note: {exc}")

    threading.Thread(target=_launch, daemon=True, name="FireTVLauncher").start()


# ============================================================================
# 🎯 DETECTION MODE ENUM & CONFIGURATION FLAG
# ============================================================================
class DetectionMode(str, Enum):
    HYBRID = "hybrid"      # Local model (MODEL_ID) on every frame + Workflow verification on count change
    MODEL = "model"        # Local model (MODEL_ID) only on every frame
    WORKFLOW = "workflow"  # Roboflow workflow (general-segmentation-api-3) only


# >>> SET YOUR DEFAULT EXECUTION MODE HERE <<<
CURRENT_MODE: DetectionMode = DetectionMode.HYBRID
# ============================================================================

# Model & verification parameters
LOCAL_YOLO_PATH = ROOT / "models" / "unified_detector" / "weights" / "best.pt"
MODEL_ID = "packages-v4/6"
CONFIDENCE = 0.35
IOU_THRESHOLD = 0.40  # NMS IoU threshold (e.g. 0.30 - 0.60) to tune overlapping box suppression

# Debounce time before spending a workflow call or confirming local model count change
CHANGE_CONFIRM_SECONDS = 0.25

# Cooldown before retrying a previously rejected candidate
RETRY_SECONDS = 3.0

# Timeout when waiting for in-flight verification at video end
FINAL_WAIT_SECONDS = 10.0


def invoke_alert(event_type: str = "PACKAGE_REMOVAL", details: dict = None, frame = None):
    print("🚨 ALERT: Package removal confirmed!")
    payload = {
        "event": event_type,
        "timestamp": time.time(),
        "details": details or {},
    }
    send_alert_webhook(payload=payload, frame=frame)


class WorkflowVerifier:
    """Runs workflow verification in a background thread (one job at a time)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._job = None
        self._result = None
        self.busy = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, frame, candidate=None, video_time=None):
        with self._lock:
            if self.busy:
                return False
            self._job = (frame.copy(), candidate, video_time)
            self.busy = True
            return True

    def poll(self):
        """Returns (candidate, workflow_pkgs, frame, video_time) once, or None."""
        with self._lock:
            result, self._result = self._result, None
            return result

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def _worker(self):
        while not self._stop.is_set():
            with self._lock:
                job, self._job = self._job, None

            if job is None:
                time.sleep(0.01)
                continue

            frame, candidate, video_time = job
            try:
                # model=None calls the hosted general-segmentation-api-3 workflow
                workflow_pkgs = detect_packages(frame, model=None, confidence=CONFIDENCE)
            except Exception as exc:
                print(f"Workflow execution failed: {exc}")
                workflow_pkgs = None

            with self._lock:
                self._result = (candidate, workflow_pkgs, frame, video_time)
                self.busy = False


class VerifiedPackageCounter:
    """
    [HYBRID MODE]
    - Local model (MODEL_ID) runs on every frame to propose candidate count.
    - When candidate differs from confirmed count, a frame is extracted and verified
      with the Roboflow general-segmentation-api-3 workflow.
    - If workflow confirms the candidate, verified count is updated.
    - If false candidate (e.g. glitch to 3 packages when only 2), rejected.
    - If confirmed count decreases after being established, alert is triggered.
    """

    def __init__(self, verifier, enable_default_alert: bool = False):
        self.verifier = verifier
        self.enable_default_alert = enable_default_alert
        self.confirmed = 0
        self.last_status = "Initialized"
        self.alert_triggered = False
        self._established = False
        self._pending = None
        self._pending_since = 0.0
        self._rejected_at = {}
        self.verified_changes = []
        self._new_verified = []

    def poll_verified_changes(self):
        """Returns and clears all newly verified change events."""
        events = list(self._new_verified)
        self._new_verified.clear()
        return events

    def reset_for_new_cycle(self):
        """Reset internal state when video loops to restart detection cycle cleanly."""
        self.confirmed = 0
        self.last_status = "Loop cycle restarted"
        self.alert_triggered = False
        self._established = False
        self._pending = None
        self._pending_since = 0.0
        self._rejected_at.clear()
        self.verified_changes.clear()
        self._new_verified.clear()

    def update(self, candidate, frame, video_time=None):
        self._apply_result()

        if candidate == self.confirmed:
            self._pending = None
            return

        now = time.monotonic()

        if candidate != self._pending:
            self._pending = candidate
            self._pending_since = now
            return

        if now - self._pending_since < CHANGE_CONFIRM_SECONDS:
            return

        if now - self._rejected_at.get(candidate, float("-inf")) < RETRY_SECONDS:
            return

        if self.verifier.submit(frame, candidate, video_time=video_time):
            self.last_status = f"Verifying {candidate} pkg(s) with Roboflow..."
            print(
                f"[Hybrid] Local model proposed {candidate} package(s) (confirmed: {self.confirmed}). "
                "Extracting frame and verifying with Roboflow workflow..."
            )

    def finish(self):
        waited = 0.0
        while self.verifier.busy and waited < FINAL_WAIT_SECONDS:
            time.sleep(0.1)
            waited += 0.1
        self._apply_result()

    def _apply_result(self):
        result = self.verifier.poll()
        if result is None:
            return

        candidate, workflow_pkgs, frame, *rest = result
        video_time = rest[0] if rest else None
        now = time.monotonic()

        if workflow_pkgs is None:
            self._rejected_at[candidate] = now
            self.last_status = "Workflow error (timed out)"
            return

        workflow_count = len(workflow_pkgs)

        if workflow_count == candidate:
            previous = self.confirmed
            self.confirmed = candidate
            self.last_status = f"Verified: {candidate} pkg(s)"
            print(f"✅ [Hybrid] Verified: package count {previous} -> {candidate} (confirmed by workflow)")
            if self._established and candidate < previous and self.enable_default_alert:
                self.alert_triggered = True
                invoke_alert(
                    event_type="PACKAGE_REMOVAL",
                    details={
                        "previous_count": previous,
                        "current_count": candidate,
                        "packages_removed": previous - candidate,
                        "mode": "hybrid",
                    },
                    frame=frame,
                )
            self._established = True
            change_event = {
                "timestamp": video_time if video_time is not None else time.time(),
                "package_count": candidate,
                "frame": frame,
                "prev_count": previous,
            }
            self.verified_changes.append(change_event)
            self._new_verified.append(change_event)
        else:
            self._rejected_at[candidate] = now
            self.last_status = f"Rejected: local {candidate} != workflow {workflow_count}"
            print(
                f"❌ [Hybrid] Rejected false count: local model proposed {candidate}, "
                f"but workflow confirmed {workflow_count}. Total package count remains {self.confirmed}."
            )


class ModelPackageCounter:
    """
    [MODEL MODE]
    Uses ONLY local model (MODEL_ID) with temporal debounce.
    Updates package count directly and triggers alert when count decreases.
    """

    def __init__(self):
        self.confirmed = 0
        self.alert_triggered = False
        self._established = False
        self._pending = None
        self._pending_since = 0.0

    def update(self, candidate):
        if candidate == self.confirmed:
            self._pending = None
            return

        now = time.monotonic()
        if candidate != self._pending:
            self._pending = candidate
            self._pending_since = now
            return

        if now - self._pending_since >= CHANGE_CONFIRM_SECONDS:
            previous = self.confirmed
            self.confirmed = candidate
            print(f"📦 [Model] Local model count updated: {previous} -> {candidate}")
            if self._established and candidate < previous:
                self.alert_triggered = True
                invoke_alert(
                    event_type="PACKAGE_REMOVAL",
                    details={
                        "previous_count": previous,
                        "current_count": candidate,
                        "packages_removed": previous - candidate,
                        "mode": "model",
                    },
                )
            self._established = True
            self._pending = None

    def finish(self):
        pass


class WorkflowPackageCounter:
    """
    [WORKFLOW MODE]
    Uses ONLY the Roboflow general-segmentation-api-3 workflow.
    Dispatches frames to background worker and updates count when results arrive.
    Triggers alert when verified count decreases.
    """

    def __init__(self, verifier):
        self.verifier = verifier
        self.confirmed = 0
        self.alert_triggered = False
        self.last_pkgs = []
        self._established = False

    def update(self, frame):
        result = self.verifier.poll()
        if result is not None:
            candidate, workflow_pkgs, *_ = result
            if workflow_pkgs is not None:
                self.last_pkgs = workflow_pkgs
                count = len(workflow_pkgs)
                if count != self.confirmed or not self._established:
                    previous = self.confirmed
                    self.confirmed = count
                    print(f"📦 [Workflow] Roboflow workflow count: {previous} -> {count}")
                    if self._established and count < previous:
                        self.alert_triggered = True
                        invoke_alert(
                            event_type="PACKAGE_REMOVAL",
                            details={
                                "previous_count": previous,
                                "current_count": count,
                                "packages_removed": previous - count,
                                "mode": "workflow",
                            },
                            frame=frame,
                        )
                    self._established = True

        # Keep submitting frames if worker is idle
        if frame is not None and not self.verifier.busy:
            self.verifier.submit(frame, candidate=None)

    def finish(self):
        waited = 0.0
        while self.verifier.busy and waited < FINAL_WAIT_SECONDS:
            time.sleep(0.1)
            waited += 0.1
        self.update(None)


def monitor_video(
    video_path,
    mode: DetectionMode = CURRENT_MODE,
    model_id: str = MODEL_ID,
    confidence: float = CONFIDENCE,
    iou_threshold: float = IOU_THRESHOLD,
):
    if isinstance(mode, str):
        mode = DetectionMode(mode.lower())

    print("\n" + "=" * 55)
    print("▶ Starting Video Monitoring")
    print(f"▶ Mode:     {mode.value.upper()}")
    print(f"▶ Video:    {video_path}")
    if mode in (DetectionMode.HYBRID, DetectionMode.MODEL):
        print(f"▶ Model ID: {model_id}")
    if mode in (DetectionMode.HYBRID, DetectionMode.WORKFLOW):
        print("▶ Workflow: general-segmentation-api-3")
    print("=" * 55 + "\n")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frame_delay = 1.0 / fps

    # Initialize local model if needed
    model = None
    if mode in (DetectionMode.HYBRID, DetectionMode.MODEL):
        if LOCAL_YOLO_PATH.exists():
            from ultralytics import YOLO
            print(f"📦 Loading Unified Local Model: {LOCAL_YOLO_PATH.name}")
            model = YOLO(str(LOCAL_YOLO_PATH))
        else:
            print(f"📦 Loading Roboflow Model: {model_id}")
            model = get_model(model_id=model_id, api_key=os.getenv("ROBOFLOW_API_KEY"))

    # Initialize workflow verifier if needed
    verifier = None
    if mode in (DetectionMode.HYBRID, DetectionMode.WORKFLOW):
        verifier = WorkflowVerifier()

    # Initialize counter corresponding to mode
    if mode == DetectionMode.HYBRID:
        counter = VerifiedPackageCounter(verifier, enable_default_alert=False)
    elif mode == DetectionMode.MODEL:
        counter = ModelPackageCounter()
    elif mode == DetectionMode.WORKFLOW:
        counter = WorkflowPackageCounter(verifier)

    candidate = 0
    cycle_count = 1
    frame_idx = 0
    stable_history: List[StableObservation] = []
    rule_states: Dict[str, RuleState] = {}
    fired_this_cycle: Set[str] = set()
    prev_active_rule_ids: Set[str] = set()
    last_alert_msg = ""
    alert_banner_until = 0.0
    visual_monitor = VisualRuleMonitor(vlm_reasoner)

    window_title = "Smart Home Porch Camera - Live Monitoring"
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_title, 1024, 576)

    initial_rules = get_live_rules()
    prev_active_rule_ids = set(r.id for r in initial_rules)
    print(f"\n{'='*75}")
    print(f"🔄 [Video Loop] Cycle #1 STARTED | File: {Path(video_path).name} ({cap.get(cv2.CAP_PROP_FRAME_COUNT):.0f} frames @ {fps:.1f} FPS)")
    print(f"   Active Armed Rules ({len(initial_rules)}): {[r.id for r in initial_rules]}")
    print(f"{'='*75}\n")

    try:
        while True:
            started = time.monotonic()

            ok, frame = cap.read()
            if not ok:
                # Video reached end -> Rewind back to frame 0 and loop continuously!
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                if mode == DetectionMode.HYBRID:
                    counter.reset_for_new_cycle()
                stable_history.clear()
                rule_states.clear()
                fired_this_cycle.clear()
                visual_monitor.reset()
                vlm_reasoner.cache.clear()
                print(f"\n{'='*75}")
                print(f"🔄 [Video Loop] Cycle #{cycle_count} COMPLETE ({frame_idx} frames processed).")
                print(f"🔄 [Video Loop] Rewinding back to frame 0 with clean state reset.")
                cycle_count += 1
                frame_idx = 0
                active_rules_now = get_live_rules()
                print(f"🔄 [Video Loop] Cycle #{cycle_count} STARTING | Armed Rules ({len(active_rules_now)}): {[r.id for r in active_rules_now]}")
                print(f"{'='*75}\n")
                continue

            frame_idx += 1
            video_time = frame_idx / fps
            boxes_to_draw = []

            # Check for newly added/removed rules from Alexa UI
            active_rules = get_live_rules()
            current_rule_ids = set(r.id for r in active_rules)
            if current_rule_ids != prev_active_rule_ids:
                diff_added = current_rule_ids - prev_active_rule_ids
                diff_removed = prev_active_rule_ids - current_rule_ids
                if diff_added:
                    print(f"📋 [Rules Engine] New rule armed from Alexa UI: {list(diff_added)} | Total active: {len(active_rules)}")
                if diff_removed:
                    print(f"🗑️ [Rules Engine] Rule removed from engine: {list(diff_removed)} | Total active: {len(active_rules)}")
                prev_active_rule_ids = current_rule_ids

            # 1. Mode-specific processing
            if mode == DetectionMode.HYBRID:
                raw_detections = detect_packages(
                    frame, model=model, confidence=confidence, iou_threshold=iou_threshold
                )
                packages = [d for d in raw_detections if d.class_name == "package"]
                candidate = len(packages)
                counter.update(candidate, frame, video_time=round(video_time, 3))
                boxes_to_draw = raw_detections

                def _on_visual_alert(event, cycle=cycle_count):
                    nonlocal last_alert_msg, alert_banner_until
                    if not any(r.id == event["rule_id"] for r in get_live_rules()):
                        return
                    last_alert_msg = event["alert"]
                    alert_banner_until = time.time() + 5.0
                    timeline_store.log_event("visual_rule_alert", event)
                    print(f"🚨 [VISUAL RULE ALERT] track={event['track_id']} rule={event['rule_id']}")
                    send_alert_webhook(
                        payload={"event": "RULE_ALERT_FIRED", "timestamp": time.time(),
                                 **event, "value": 1, "cycle": cycle},
                        frame=None, sync=False,
                    )

                visual_monitor.update(
                    frame, raw_detections, active_rules, video_time, _on_visual_alert,
                    on_detection=lambda event: timeline_store.log_event(f"{event['source']}_detected", event),
                )

                # Dynamic Rule Evaluator for active rules from Alexa / MCP
                new_verified = counter.poll_verified_changes()
                for change in new_verified:
                    print(f"\n📊 [Verified Count Change] Frame {frame_idx:3d} (t={video_time:5.2f}s) | Confirmed packages: {change['package_count']}")
                    timeline_store.log_event("package_count_change", {
                        "package_count": change["package_count"],
                        "video_time": change["timestamp"],
                    })
                    obs = StableObservation(
                        timestamp=change["timestamp"],
                        package_count=change["package_count"],
                    )
                    stable_history.append(obs)

                    pkg_rules = [r for r in active_rules if r.source == "package_count" and r.derive is not None]
                    if not pkg_rules:
                        print("ℹ️ [Rule Evaluator] No active package rules armed.")
                    else:
                        print(f"🔍 [Rule Evaluator] Evaluating {len(pkg_rules)} active package rule(s)...")
                        for rule in pkg_rules:
                            if rule.id not in rule_states:
                                rule_states[rule.id] = RuleState()
                            decision, new_state = evaluate(
                                rule, stable_history, rule_states[rule.id]
                            )
                            rule_states[rule.id] = new_state

                            status_symbol = "🔥 TRIGGERED" if decision.fired else "✓ OK"
                            print(f"   • Rule '{rule.id}': {status_symbol} (Metric Value={decision.value})")

                            if decision.fired and rule.id not in fired_this_cycle:
                                fired_this_cycle.add(rule.id)
                                alert_text = decision.alert_text or f"Condition met for rule '{rule.id}'"
                                if not alert_text.startswith("🚨"):
                                    alert_text = f"🚨 {alert_text}"

                                print(f"\n{'='*75}")
                                print(f"🚨 [RULE TRIGGERED] Cycle #{cycle_count} at t={decision.timestamp:.2f}s")
                                print(f"   Rule:        {rule.id}")
                                print(f"   Alert Text:  {alert_text}")
                                print(f"   Value:       {decision.value}")
                                print(f"   Action:      Forwarding alert to Fire TV WebSocket via Webhook...")
                                print(f"{'='*75}\n")

                                last_alert_msg = f"[{rule.id}] {alert_text}"
                                alert_banner_until = time.time() + 5.0

                                timeline_store.log_event("rule_alert", {
                                    "rule_id": rule.id,
                                    "alert": alert_text,
                                    "video_time": decision.timestamp,
                                    "value": decision.value,
                                })

                                # Send webhook to Fire TV!
                                send_alert_webhook(
                                    payload={
                                        "event": "RULE_ALERT_FIRED",
                                        "timestamp": time.time(),
                                        "video_time": decision.timestamp,
                                        "rule_id": rule.id,
                                        "alert": alert_text,
                                        "value": decision.value,
                                        "cycle": cycle_count,
                                    },
                                    frame=None,
                                    sync=False,
                                )

            elif mode == DetectionMode.MODEL:
                packages = detect_packages(
                    frame, model=model, confidence=confidence, iou_threshold=iou_threshold
                )
                candidate = len(packages)
                counter.update(candidate)
                boxes_to_draw = packages

            elif mode == DetectionMode.WORKFLOW:
                counter.update(frame)
                boxes_to_draw = counter.last_pkgs

            # 2. Render bounding boxes for packages, persons, cars
            color_map = {
                "package": (0, 255, 0),      # Green
                "person": (255, 180, 0),     # Cyan / Blue
                "car": (0, 255, 255),        # Yellow
            }
            for obj in boxes_to_draw:
                x1, y1, x2, y2 = obj.box_xyxy
                box_color = color_map.get(obj.class_name, (0, 255, 0))
                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                cv2.putText(
                    frame,
                    f"{obj.class_name} {obj.confidence:.2f}" + (f" #{obj.track_id}" if obj.track_id is not None else ""),
                    (x1, max(25, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    box_color,
                    2,
                )


            # 3. HUD Overlay
            active_rules_list = get_live_rules()
            if active_rules_list:
                rules_text = f"Armed Rules: {len(active_rules_list)} active"
                rules_color = (56, 189, 248)
            else:
                rules_text = "Armed Rules: 0 (Waiting for Alexa)"
                rules_color = (160, 160, 160)

            cv2.putText(
                frame,
                f"Mode: {mode.value.upper()} | Loop Cycle: #{cycle_count}",
                (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.75,
                (255, 200, 0),
                2,
            )
            cv2.putText(
                frame,
                rules_text,
                (max(20, frame.shape[1] - 420), 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                rules_color,
                2,
            )

            # Package Counts per mode
            if mode == DetectionMode.HYBRID:
                status_text = "Verifying frame with Roboflow..." if verifier.busy else counter.last_status
                status_color = (0, 165, 255) if verifier.busy else (0, 255, 0)

                cv2.putText(
                    frame,
                    f"Packages (Verified): {counter.confirmed}",
                    (20, 65),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (0, 255, 0) if counter.confirmed > 0 else (200, 200, 200),
                    2,
                )
                cv2.putText(
                    frame,
                    f"Local Proposed ({model_id}): {candidate}",
                    (20, 95),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 0),
                    2,
                )
                cv2.putText(
                    frame,
                    f"Status: {status_text}",
                    (20, 125),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    status_color,
                    2,
                )

            elif mode == DetectionMode.MODEL:
                cv2.putText(
                    frame,
                    f"Packages ({model_id}): {counter.confirmed}",
                    (20, 65),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (0, 255, 0) if counter.confirmed > 0 else (200, 200, 200),
                    2,
                )
                cv2.putText(
                    frame,
                    f"Raw Detections: {candidate}",
                    (20, 95),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 0),
                    2,
                )

            elif mode == DetectionMode.WORKFLOW:
                status_text = "Evaluating frame with Roboflow..." if verifier.busy else "Idle"
                status_color = (0, 165, 255) if verifier.busy else (0, 255, 0)

                cv2.putText(
                    frame,
                    f"Packages (Workflow): {counter.confirmed}",
                    (20, 65),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (0, 255, 0) if counter.confirmed > 0 else (200, 200, 200),
                    2,
                )
                cv2.putText(
                    frame,
                    f"Status: {status_text}",
                    (20, 95),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    status_color,
                    2,
                )

            # Active Alert Banner Overlay on Video
            if time.time() < alert_banner_until:
                cv2.rectangle(
                    frame,
                    (10, frame.shape[0] - 65),
                    (frame.shape[1] - 10, frame.shape[0] - 15),
                    (0, 0, 200),
                    -1,
                )
                cv2.putText(
                    frame,
                    f"ALERT: {last_alert_msg}",
                    (25, frame.shape[0] - 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.75,
                    (255, 255, 255),
                    2,
                )
            elif counter.alert_triggered:
                cv2.putText(
                    frame,
                    "ALERT: PACKAGE REMOVAL CONFIRMED!",
                    (20, 165),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    (0, 0, 255),
                    3,
                )

            cv2.imshow(window_title, frame)

            # Keep playback close to real time despite inference cost
            elapsed = time.monotonic() - started
            wait_ms = max(1, int((frame_delay - elapsed) * 1000))
            key = cv2.waitKey(wait_ms) & 0xFF
            if key in (ord("q"), 27):  # 'q' or ESC
                print("\n⏹️ [Video Loop] Exit requested by user.")
                break

        counter.finish()
        print(f"\nFinal verified package count ({mode.value} mode): {counter.confirmed}")

    finally:
        visual_monitor.reset()
        if verifier is not None:
            verifier.stop()
        cap.release()
        cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser(
        description="Smart Home Package Defense & Automation System"
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in DetectionMode],
        default=os.getenv("DETECTION_MODE", CURRENT_MODE.value),
        help=f"Detection mode: {[m.value for m in DetectionMode]} (default: {CURRENT_MODE.value})",
    )
    parser.add_argument(
        "--model-id",
        default=MODEL_ID,
        help=f"Roboflow model ID for local inference (default: {MODEL_ID})",
    )
    parser.add_argument(
        "--video",
        default=str(VIDEO_PATH),
        help=f"Path to input video file (default: {VIDEO_PATH})",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=CONFIDENCE,
        help=f"Confidence threshold (default: {CONFIDENCE})",
    )
    parser.add_argument(
        "--iou-threshold",
        type=float,
        default=IOU_THRESHOLD,
        help=f"NMS IoU threshold for overlapping boxes (default: {IOU_THRESHOLD})",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Do not automatically launch Alexa Streamlit UI",
    )
    parser.add_argument(
        "--no-tv",
        action="store_true",
        help="Do not automatically invoke Fire TV app",
    )
    args = parser.parse_args()

    import atexit
    clear_all_rules_everywhere()
    atexit.register(clear_all_rules_everywhere)

    print("\n" + "=" * 60)
    print("⚡ SMART HOME AI DEFENSE SYSTEM INITIALIZING")
    print("=" * 60)

    # 1. Ensure backend is active
    ensure_backend_active()

    # 2. Ensure Alexa UI is active & opened in browser
    if not args.no_ui:
        ensure_alexa_ui_active()

    # 3. Invoke Fire TV app in background
    if not args.no_tv:
        invoke_firetv_app()

    print("=" * 60)
    print("▶ Starting live video loop window... (Press 'q' in window to exit)")
    print("=" * 60 + "\n")

    mode_enum = DetectionMode(args.mode.lower())
    try:
        monitor_video(
            args.video,
            mode=mode_enum,
            model_id=args.model_id,
            confidence=args.confidence,
            iou_threshold=args.iou_threshold,
        )
    finally:
        clear_all_rules_everywhere()


if __name__ == "__main__":
    main()
