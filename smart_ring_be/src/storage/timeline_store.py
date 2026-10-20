from datetime import datetime, time as dtime
import threading
import time
from typing import Any, Dict, List, Optional


class TimelineStore:
    """
    In-memory time-series event store for video monitoring history and deadline scheduling.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.events: List[Dict[str, Any]] = []
        self.deadlines: List[Dict[str, Any]] = []

    def log_event(self, event_type: str, details: Optional[Dict[str, Any]] = None):
        """Records a verified event with wall-clock and human-readable time."""
        now = datetime.now()
        with self._lock:
            entry = {
                "id": len(self.events) + 1,
                "timestamp": time.time(),
                "time_str": now.strftime("%H:%M:%S"),
                "date_str": now.strftime("%Y-%m-%d"),
                "event_type": event_type,
                "details": details or {},
            }
            self.events.append(entry)
            print(f"📜 [Timeline Log] Recorded event: {event_type} at {entry['time_str']}")
            return entry

    def query_history(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query past events within an optional time range (HH:MM or HH:MM:SS) and event filter.
        """
        with self._lock:
            results = list(self.events)

        if event_type:
            event_type_lower = event_type.lower()
            results = [e for e in results if event_type_lower in e["event_type"].lower()]

        def parse_t(t_str: str) -> Optional[dtime]:
            for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p", "%I %p"):
                try:
                    return datetime.strptime(t_str.strip(), fmt).time()
                except ValueError:
                    continue
            return None

        if start_time:
            st = parse_t(start_time)
            if st:
                results = [e for e in results if parse_t(e["time_str"]) and parse_t(e["time_str"]) >= st]

        if end_time:
            et = parse_t(end_time)
            if et:
                results = [e for e in results if parse_t(e["time_str"]) and parse_t(e["time_str"]) <= et]

        return results

    def add_deadline(self, expected_event: str, deadline_time: str, message: Optional[str] = None) -> Dict[str, Any]:
        """
        Schedules a deadline alert (e.g. 'Alert if package did not arrive by 14:00').
        """
        with self._lock:
            dl = {
                "id": f"dl_{len(self.deadlines) + 1}",
                "expected_event": expected_event,
                "deadline_time": deadline_time,
                "message": message or f"Expected event '{expected_event}' did not occur by {deadline_time}",
                "fired": False,
                "created_at": time.time(),
            }
            self.deadlines.append(dl)
            print(f"⏰ [Timeline] Armed deadline: '{expected_event}' by {deadline_time}")
            return dl

    def check_deadlines(self, alert_callback) -> List[Dict[str, Any]]:
        """Checks if any active deadline has passed without the required event."""
        now = datetime.now()
        current_time = now.time()
        fired_list = []

        with self._lock:
            for dl in self.deadlines:
                if dl["fired"]:
                    continue

                # Parse deadline time
                deadline_t = None
                for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p", "%I %p"):
                    try:
                        deadline_t = datetime.strptime(dl["deadline_time"].strip(), fmt).time()
                        break
                    except ValueError:
                        continue

                if deadline_t and current_time >= deadline_t:
                    # Check if expected event ever occurred before deadline
                    occurred = any(
                        dl["expected_event"].lower() in e["event_type"].lower()
                        for e in self.events
                    )
                    if not occurred:
                        dl["fired"] = True
                        fired_list.append(dl)
                        if alert_callback:
                            alert_callback(dl["message"])

        return fired_list


timeline_store = TimelineStore()
