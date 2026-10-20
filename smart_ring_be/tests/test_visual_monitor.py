from unittest.mock import patch
import threading

import httpx
import numpy as np
import pytest

from src.evaluator.models import Rule
from src.vision.package_detection import PackageDetection
from src.vision.tracking import ObjectTracker
from src.vision.visual_monitor import VisualRuleMonitor
from src.vision.vlm_reasoner import VLMReasoner


FRAME = np.zeros((400, 600, 3), dtype=np.uint8)


def person(track_id, x=120):
    return PackageDetection(x, 180, 100, 220, 0.95, "person", track_id=track_id)


def rule(rule_id="carrying", fire="every_time"):
    return Rule(id=rule_id, source="person", vlm_query="Is this person carrying a box? Answer YES or NO.",
                alert={"fire": fire, "cooldown_seconds": 5})


class PreserveIDs:
    def update(self, detections, frame):
        return detections

    def reset(self):
        pass


class Worker:
    def __init__(self):
        self.jobs = []
        self.busy = False

    def query_boolean_async(self, crop, query, key, callback):
        if self.busy:
            return False
        self.busy = True
        self.jobs.append((key, callback))
        return True

    def finish(self, answer):
        self.jobs[-1][1](self.jobs[-1][0], answer, str(answer))
        self.busy = False


@pytest.fixture
def setup_monitor():
    clock = [0.0]
    worker = Worker()
    monitor = VisualRuleMonitor(worker, tracker=PreserveIDs(), interval=2, clock=lambda: clock[0])
    alerts = []

    def tick(at, people=None, rules=None):
        clock[0] = at
        monitor.update(FRAME, people if people is not None else [person(1)],
                       rules if rules is not None else [rule()], at, alerts.append)

    return monitor, worker, alerts, tick


def test_two_people_can_each_alert_even_inside_global_cooldown(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    tick(0, [person(1), person(2, 320)])
    worker.finish(True)
    tick(0.1, [person(1), person(2, 320)])
    worker.finish(True)
    assert [alert["track_id"] for alert in alerts] == [1, 2]


def test_empty_handed_then_carrying_rechecks_and_alerts(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    tick(0)
    worker.finish(False)
    tick(1)
    assert len(worker.jobs) == 1
    tick(2)
    worker.finish(True)
    assert len(alerts) == 1


def test_continuous_carrying_does_not_repeat_but_new_episode_alerts(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    for at, answer in [(0, True), (2, True), (6, True), (8, False), (10, True)]:
        tick(at)
        worker.finish(answer)
    assert len(alerts) == 2


def test_one_pending_check_and_fair_service_after_busy_worker(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    people = [person(1), person(2, 320)]
    tick(0, people)
    tick(1, people)
    tick(2, people)
    assert len(worker.jobs) == 1
    worker.finish(False)
    tick(3, people)
    assert ":person:2:" in worker.jobs[-1][0]


def test_rule_fairness_for_same_person(setup_monitor):
    _, worker, _, tick = setup_monitor
    rules = [rule("first"), rule("second")]
    tick(0, rules=rules)
    worker.finish(False)
    tick(0.1, rules=rules)
    assert worker.jobs[-1][0].endswith(":second")


def test_unknown_result_does_not_rearm_episode(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    for at, answer in [(0, True), (2, None), (6, True)]:
        tick(at)
        worker.finish(answer)
    assert len(alerts) == 1


def test_episode_waits_for_its_own_cooldown(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    for at, answer in [(0, True), (2, False), (4, True), (6, True)]:
        tick(at)
        worker.finish(answer)
    assert len(alerts) == 2


@pytest.mark.parametrize("invalidate", ["reset", "removed", "edited", "expired"])
def test_late_results_cannot_fire_after_invalidation(setup_monitor, invalidate):
    monitor, worker, alerts, tick = setup_monitor
    tick(0)
    if invalidate == "reset":
        monitor.reset()
    elif invalidate == "removed":
        tick(1, rules=[])
    elif invalidate == "edited":
        edited = rule().model_copy(update={"vlm_query": "Is this person wearing red? Answer YES or NO."})
        tick(1, rules=[edited])
    else:
        tick(6, people=[])
    worker.finish(True)
    assert alerts == []


def test_brief_occlusion_keeps_activity_state(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    tick(0)
    worker.finish(True)
    tick(1, people=[])
    tick(3)
    worker.finish(True)
    assert len(alerts) == 1


def test_small_visible_person_keeps_state_without_sending_unusable_crops(setup_monitor):
    _, worker, alerts, tick = setup_monitor
    tick(0)
    worker.finish(True)
    small = PackageDetection(120, 180, 30, 50, 0.95, "person", track_id=1)
    for at in (2, 4, 6):
        tick(at, [small])
    assert len(worker.jobs) == 1
    tick(8)
    worker.finish(True)
    assert len(alerts) == 1


def test_detection_history_is_logged_once_per_track_without_visual_rules(setup_monitor):
    monitor, _, _, _ = setup_monitor
    events = []
    for _ in range(3):
        monitor.update(FRAME, [person(1), person(2, 320)], [], 0, lambda event: None, events.append)
    assert [event["track_id"] for event in events] == [1, 2]


def test_once_ever_is_rule_wide_and_survives_video_rewind(setup_monitor):
    monitor, worker, alerts, tick = setup_monitor
    rules = [rule(fire="once_ever")]
    tick(0, rules=rules)
    worker.finish(True)
    tick(1, [person(2)], rules)
    worker.finish(True)
    monitor.reset()
    tick(2, rules=rules)
    worker.finish(True)
    assert len(alerts) == 1


def test_actual_botsort_keeps_ids_and_distinguishes_people():
    tracker = ObjectTracker()
    for shift in (0, 3, 6):
        objects = [person(None, 120 + shift), person(None, 330 + shift)]
        tracker.update(objects, FRAME)
        current = [obj.track_id for obj in objects]
        assert None not in current and current[0] != current[1]
        if shift == 0:
            expected = current
        assert current == expected
    # Age old tracks out, then a person at the same location is a new appearance.
    for _ in range(65):
        tracker.update([], FRAME)
    objects = [person(None, 126)]
    tracker.update(objects, FRAME)
    tracker.update(objects, FRAME)
    assert objects[0].track_id is not None
    assert objects[0].track_id not in expected


@pytest.mark.parametrize("raw,expected", [("YES", True), ("NO.", False),
                                         ("Not sure, maybe yes", None)])
def test_reasoner_strict_answers_and_atomic_admission(raw, expected):
    reasoner = VLMReasoner()
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    answers = []

    def post(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"response": raw})

    def callback(key, matched, response):
        answers.append(matched)
        completed.set()

    with patch("src.vision.vlm_reasoner.httpx.Client.post", post):
        assert reasoner.query_boolean_async(FRAME, "question", "1", callback)
        assert entered.wait(3)
        try:
            assert reasoner.query_boolean_async(FRAME, "question", "1", callback) is False
        finally:
            release.set()
        assert completed.wait(3)
    assert answers == [expected]


def test_reasoner_failure_completes_pending_request_as_unknown():
    reasoner = VLMReasoner()
    completed = threading.Event()
    answers = []

    def callback(key, answer, raw):
        answers.append(answer)
        completed.set()

    with patch("src.vision.vlm_reasoner.httpx.Client.post", return_value=httpx.Response(503)):
        assert reasoner.query_boolean_async(FRAME, "question", "1", callback)
        assert completed.wait(3)
    assert answers == [None]
