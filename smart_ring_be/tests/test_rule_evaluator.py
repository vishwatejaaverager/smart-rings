import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.evaluator.derivation import derive_change_events
from src.evaluator.engine import evaluate
from src.evaluator.models import Observation, RuleState, StableObservation
from src.evaluator.rules import get_hardcoded_rules
from src.evaluator.stabilizer import StabilizerState, stabilize, stabilizer_step


def test_rule_validation():
    """Verify all 3 hardcoded rules validate against Pydantic model."""
    rules = get_hardcoded_rules()
    assert len(rules) == 3
    rule_ids = [r.id for r in rules]
    assert "disappears" in rule_ids
    assert "three_new_arrivals" in rule_ids
    assert "fewer_than_two" in rule_ids


def test_disappears_rule():
    """disappears: 3->2 fires; 2->2 no; 2->3 no; staying at 2 over repeated observations does NOT re-fire."""
    rules = {r.id: r for r in get_hardcoded_rules()}
    rule = rules["disappears"]

    # Stable counts fed directly:
    # 3 (baseline) -> 2 -> 2 -> 3 -> 2 -> 2
    counts = [3, 2, 2, 3, 2, 2]
    stable_history = []
    state = RuleState()
    fired_at_indices = []

    for idx, c in enumerate(counts):
        stable_history.append(StableObservation(timestamp=idx * 1.0, package_count=c))
        decision, state = evaluate(rule, stable_history, state)
        if decision.fired:
            fired_at_indices.append(idx)
            assert "Package count dropped" in decision.alert_text

    # 3->2 (index 1) fires
    # 2->2 (index 2) no
    # 2->3 (index 3) no
    # 3->2 (index 4) fires
    # 2->2 (index 5) staying at 2 does NOT re-fire
    assert fired_at_indices == [1, 4]


def test_three_new_arrivals_rule():
    """three_new_arrivals, counts 1,2,4,3,5: total positive increase = 1+2+0+2 = 5,

    fires exactly once, at the 4, and never again.
    """
    rules = {r.id: r for r in get_hardcoded_rules()}
    rule = rules["three_new_arrivals"]

    counts = [1, 2, 4, 3, 5]
    stable_history = []
    state = RuleState()
    fired_at_indices = []
    values_at_fired = []

    for idx, c in enumerate(counts):
        stable_history.append(StableObservation(timestamp=idx * 1.0, package_count=c))
        decision, state = evaluate(rule, stable_history, state)
        if decision.fired:
            fired_at_indices.append(idx)
            values_at_fired.append(decision.value)

    # Fires exactly once at count 4 (index 2) where cumulative positive increase reached 3
    assert fired_at_indices == [2]
    assert values_at_fired == [3]

    # Verify total positive increase over the entire sequence is 5
    all_events = derive_change_events(stable_history)
    pos_deltas = [ev.delta for ev in all_events if ev.delta > 0]
    assert sum(pos_deltas) == 5  # (2-1) + (4-2) + 0 + (5-3) = 1 + 2 + 0 + 2 = 5


def test_fewer_than_two_rule():
    """fewer_than_two: 3,2,1,1,1 fires once; then 1->2->1 fires again."""
    rules = {r.id: r for r in get_hardcoded_rules()}
    rule = rules["fewer_than_two"]

    # Sequence: 3, 2, 1, 1, 1, then 2, 1
    counts = [3, 2, 1, 1, 1, 2, 1]
    stable_history = []
    state = RuleState()
    fired_at_indices = []

    for idx, c in enumerate(counts):
        stable_history.append(StableObservation(timestamp=idx * 1.0, package_count=c))
        decision, state = evaluate(rule, stable_history, state)
        if decision.fired:
            fired_at_indices.append(idx)

    # Index 2 (first 1) fires
    # Index 3, 4 (subsequent 1s in episode) do NOT fire
    # Index 5 (count 2) re-arms
    # Index 6 (count 1) fires again
    assert fired_at_indices == [2, 6]


def test_stabilizer_with_glitches():
    """stabilizer: 3,3,2,3,3 with N=2 -> no committed change; 3,3,2,2 -> commits 3->2."""
    # Case 1: 3,3,2,3,3 with N=2
    raw_1 = [
        Observation(timestamp=0.0, package_count=3),
        Observation(timestamp=1.0, package_count=3),
        Observation(timestamp=2.0, package_count=2),
        Observation(timestamp=3.0, package_count=3),
        Observation(timestamp=4.0, package_count=3),
    ]
    stables_1 = stabilize(raw_1, n=2)
    # The committed stable counts should all be 3
    assert all(s.package_count == 3 for s in stables_1)
    changes_1 = derive_change_events(stables_1)
    assert len(changes_1) == 0  # No committed change!

    # Case 2: 3,3,2,2 with N=2
    raw_2 = [
        Observation(timestamp=0.0, package_count=3),
        Observation(timestamp=1.0, package_count=3),
        Observation(timestamp=2.0, package_count=2),
        Observation(timestamp=3.0, package_count=2),
    ]
    stables_2 = stabilize(raw_2, n=2)
    changes_2 = derive_change_events(stables_2)
    assert len(changes_2) == 1
    assert changes_2[0].prev == 3
    assert changes_2[0].curr == 2
    assert changes_2[0].delta == -1


def test_baseline_ignores_prior_increases():
    """baseline: increases are counted from the first stable count; an increase before the baseline is not counted."""
    rules = {r.id: r for r in get_hardcoded_rules()}
    rule = rules["three_new_arrivals"]

    # Raw counts go 0 -> 1 -> 1 -> 2 -> 2 with N=2
    # Baseline is established at 1! The raw jump from 0 to 1 happens before baseline is set.
    raw_observations = [
        Observation(timestamp=0.0, package_count=0),  # candidate=0, streak=1
        Observation(timestamp=1.0, package_count=1),  # candidate=1, streak=1
        Observation(timestamp=2.0, package_count=1),  # candidate=1, streak=2 -> BASELINE = 1!
        Observation(timestamp=3.0, package_count=2),  # candidate=2, streak=1
        Observation(timestamp=4.0, package_count=2),  # candidate=2, streak=2 -> commits 2!
    ]
    stables = stabilize(raw_observations, n=2)

    # First stable observation is baseline 1
    assert stables[0].package_count == 1
    # Only one change event is emitted: 1 -> 2 (delta = +1)
    changes = derive_change_events(stables)
    assert len(changes) == 1
    assert changes[0].prev == 1
    assert changes[0].curr == 2
    assert changes[0].delta == 1

    # Running through rule evaluation: sum of increases since monitoring started is 1, not 2
    state = RuleState()
    for idx, _ in enumerate(stables):
        decision, state = evaluate(rule, stables[: idx + 1], state)

    # Positive sum is 1, which does not reach threshold 3
    assert not decision.fired
    assert state.baseline == 1


def test_purity():
    """purity: same inputs give the same outputs."""
    rules = get_hardcoded_rules()
    history = [
        StableObservation(timestamp=1.0, package_count=3),
        StableObservation(timestamp=2.0, package_count=1),
    ]
    initial_state = RuleState()

    for rule in rules:
        dec1, state1 = evaluate(rule, history, initial_state)
        dec2, state2 = evaluate(rule, history, initial_state)
        assert dec1 == dec2
        assert state1 == state2
