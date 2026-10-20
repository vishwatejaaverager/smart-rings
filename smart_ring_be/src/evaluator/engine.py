from typing import Any, List, Optional, Tuple, Union

from src.evaluator.derivation import derive_change_events
from src.evaluator.models import EvaluationDecision, Rule, RuleState, StableObservation


def check_comparison(val: Union[int, float], op: str, target: Union[int, float]) -> bool:
    """Pure comparison helper."""
    if op == "<":
        return val < target
    elif op == "<=":
        return val <= target
    elif op == ">":
        return val > target
    elif op == ">=":
        return val >= target
    elif op == "==":
        return val == target
    elif op == "!=":
        return val != target
    raise ValueError(f"Unsupported comparison operator: {op}")


def generate_alert_text(
    rule: Rule, stable_history: List[StableObservation], agg_value: Any
) -> str:
    """Generate human-readable alert message when a rule fires."""
    if rule.source in ("person", "car"):
        if rule.vlm_query:
            query_clean = rule.vlm_query.replace("Answer YES or NO.", "").strip()
            return f"{rule.source.title()} event detected: {query_clean}"
        return f"{rule.source.title()} visual condition met for '{rule.id}'."

    curr = stable_history[-1].package_count if stable_history else 0
    if rule.derive and rule.derive.type == "delta":
        if (rule.id == "disappears" or rule.derive.keep == "negative") and len(stable_history) >= 2:
            prev = stable_history[-2].package_count
            return f"Package count dropped from {prev} to {curr}."
        elif rule.id == "three_new_arrivals" or rule.derive.keep == "positive":
            return (
                f"Package arrival threshold reached: {agg_value} new package(s) arrived "
                f"(current count: {curr})."
            )
    elif rule.derive and rule.derive.type == "identity" and rule.compare:
        if rule.id == "fewer_than_two" or rule.compare.op in ("<", "<="):
            return f"Package count is fewer than {rule.compare.value} (current count: {curr})."

    op_str = rule.compare.op if rule.compare else "=="
    val_str = rule.compare.value if rule.compare else agg_value
    return f"Rule '{rule.id}' alert triggered: {rule.source} {op_str} {val_str} (value: {agg_value})."


def evaluate_visual_rule(
    rule: Rule,
    condition_met: bool,
    timestamp: Union[float, int, str],
    state: Optional[RuleState] = None,
    alert_text: Optional[str] = None,
) -> Tuple[EvaluationDecision, RuleState]:
    """
    Evaluates visual rules (person, car) with cooldown and fire modes.
    """
    if state is None:
        state = RuleState()

    ts = timestamp
    in_cooldown = False
    if rule.alert.cooldown_seconds > 0 and state.last_fired_at is not None:
        try:
            if (float(ts) - float(state.last_fired_at)) < rule.alert.cooldown_seconds:
                in_cooldown = True
        except (ValueError, TypeError):
            pass

    fire_mode = rule.alert.fire
    should_fire = False

    if fire_mode == "every_time":
        should_fire = condition_met and not in_cooldown
        new_armed = state.armed
        new_fired_count = state.fired_count + (1 if should_fire else 0)
        new_last_fired = ts if should_fire else state.last_fired_at

    elif fire_mode == "once_per_episode":
        if condition_met:
            if state.armed and not in_cooldown:
                should_fire = True
                new_armed = False
                new_fired_count = state.fired_count + 1
                new_last_fired = ts
            else:
                should_fire = False
                new_armed = False
                new_fired_count = state.fired_count
                new_last_fired = state.last_fired_at
        else:
            should_fire = False
            new_armed = True
            new_fired_count = state.fired_count
            new_last_fired = state.last_fired_at

    elif fire_mode == "once_ever":
        if condition_met and state.fired_count == 0 and not in_cooldown:
            should_fire = True
            new_armed = False
            new_fired_count = 1
            new_last_fired = ts
        else:
            should_fire = False
            new_armed = False
            new_fired_count = state.fired_count
            new_last_fired = state.last_fired_at

    if not alert_text:
        if rule.vlm_query:
            clean_q = rule.vlm_query.replace("Answer YES or NO.", "").strip()
            alert_text = f"Visual alert: {clean_q}"
        else:
            alert_text = f"Visual alert triggered for rule '{rule.id}'"

    decision = EvaluationDecision(
        fired=should_fire,
        rule_id=rule.id,
        timestamp=ts if should_fire else None,
        alert_text=alert_text if should_fire else None,
        value=1 if condition_met else 0,
        condition_met=condition_met,
    )

    new_state = RuleState(
        baseline=state.baseline,
        armed=new_armed,
        last_fired_at=new_last_fired,
        fired_count=new_fired_count,
        monitoring_started_at=state.monitoring_started_at or ts,
    )

    return decision, new_state


def evaluate(
    rule: Rule,
    stable_history: List[Union[StableObservation, dict]],
    state: Optional[RuleState] = None,
) -> Tuple[EvaluationDecision, RuleState]:
    """Pure rule evaluator: evaluate(rule, stable_history, state) -> (decision, new_state)

    - No I/O, no clock reads.
    - derive.identity rules are STATE-based: evaluate on every new stable observation.
    - derive.delta rules are EVENT-based: evaluate only when a new change event arrives.
    - derive.keep filters deltas (all / positive / negative).
    - aggregate: latest | sum | count | max | min.
    - window: always | since_monitoring_started.
    - fire modes: every_time | once_per_episode | once_ever.
    """
    if state is None:
        state = RuleState()

    if rule.derive is None:
        return (
            EvaluationDecision(fired=False, rule_id=rule.id, value=None, condition_met=False),
            state,
        )

    if not stable_history:
        return (
            EvaluationDecision(fired=False, rule_id=rule.id, value=None, condition_met=False),
            state,
        )

    # Normalize stable observations
    norm_history: List[StableObservation] = [
        s
        if isinstance(s, StableObservation)
        else StableObservation(timestamp=s["timestamp"], package_count=s["package_count"])
        for s in stable_history
    ]

    current_baseline = (
        state.baseline if state.baseline is not None else norm_history[0].package_count
    )
    current_monitoring_started_at = (
        state.monitoring_started_at
        if state.monitoring_started_at is not None
        else norm_history[0].timestamp
    )

    latest_obs = norm_history[-1]
    ts = latest_obs.timestamp

    # 1. Delta rules (EVENT-BASED)
    if rule.derive.type == "delta":
        if len(norm_history) < 2:
            new_state = RuleState(
                baseline=current_baseline,
                armed=state.armed,
                last_fired_at=state.last_fired_at,
                fired_count=state.fired_count,
                monitoring_started_at=current_monitoring_started_at,
            )
            return (
                EvaluationDecision(fired=False, rule_id=rule.id, value=None, condition_met=False),
                new_state,
            )

        prev_obs = norm_history[-2]
        if latest_obs.package_count == prev_obs.package_count:
            # No change event arrived at this observation step.
            new_state = RuleState(
                baseline=current_baseline,
                armed=state.armed,
                last_fired_at=state.last_fired_at,
                fired_count=state.fired_count,
                monitoring_started_at=current_monitoring_started_at,
            )
            return (
                EvaluationDecision(fired=False, rule_id=rule.id, value=None, condition_met=False),
                new_state,
            )

        # A change event has arrived
        all_events = derive_change_events(norm_history)

        if rule.aggregate.window.type == "since_monitoring_started":
            events_in_window = [
                ev
                for ev in all_events
                if current_monitoring_started_at is None
                or ev.timestamp >= current_monitoring_started_at
            ]
        else:
            events_in_window = all_events

        # Keep filter
        if rule.derive.keep == "positive":
            deltas = [ev.delta for ev in events_in_window if ev.delta > 0]
            latest_delta = all_events[-1].delta if all_events[-1].delta > 0 else None
        elif rule.derive.keep == "negative":
            deltas = [ev.delta for ev in events_in_window if ev.delta < 0]
            latest_delta = all_events[-1].delta if all_events[-1].delta < 0 else None
        else:
            deltas = [ev.delta for ev in events_in_window]
            latest_delta = all_events[-1].delta

        # Aggregation
        if rule.aggregate.fn == "latest":
            if latest_delta is None:
                agg_value = None
                condition_met = False
            else:
                agg_value = latest_delta
                condition_met = check_comparison(agg_value, rule.compare.op, rule.compare.value)
        elif rule.aggregate.fn == "sum":
            agg_value = sum(deltas)
            condition_met = check_comparison(agg_value, rule.compare.op, rule.compare.value)
        elif rule.aggregate.fn == "count":
            agg_value = len(deltas)
            condition_met = check_comparison(agg_value, rule.compare.op, rule.compare.value)
        elif rule.aggregate.fn == "max":
            agg_value = max(deltas) if deltas else None
            condition_met = (
                False
                if agg_value is None
                else check_comparison(agg_value, rule.compare.op, rule.compare.value)
            )
        elif rule.aggregate.fn == "min":
            agg_value = min(deltas) if deltas else None
            condition_met = (
                False
                if agg_value is None
                else check_comparison(agg_value, rule.compare.op, rule.compare.value)
            )

    # 2. Identity rules (STATE-BASED)
    else:
        if rule.aggregate.window.type == "since_monitoring_started":
            obs_in_window = [
                s
                for s in norm_history
                if current_monitoring_started_at is None
                or s.timestamp >= current_monitoring_started_at
            ]
        else:
            obs_in_window = norm_history

        counts = [s.package_count for s in obs_in_window]

        if rule.aggregate.fn == "latest":
            agg_value = latest_obs.package_count
        elif rule.aggregate.fn == "sum":
            agg_value = sum(counts)
        elif rule.aggregate.fn == "count":
            agg_value = len(counts)
        elif rule.aggregate.fn == "max":
            agg_value = max(counts) if counts else None
        elif rule.aggregate.fn == "min":
            agg_value = min(counts) if counts else None

        condition_met = (
            False
            if agg_value is None
            else check_comparison(agg_value, rule.compare.op, rule.compare.value)
        )

    # Cooldown check
    in_cooldown = False
    if rule.alert.cooldown_seconds > 0 and state.last_fired_at is not None:
        try:
            if (float(ts) - float(state.last_fired_at)) < rule.alert.cooldown_seconds:
                in_cooldown = True
        except (ValueError, TypeError):
            pass

    # Fire modes: every_time | once_per_episode | once_ever
    fire_mode = rule.alert.fire
    should_fire = False

    if fire_mode == "every_time":
        should_fire = condition_met and not in_cooldown
        new_armed = state.armed
        new_fired_count = state.fired_count + (1 if should_fire else 0)
        new_last_fired = ts if should_fire else state.last_fired_at

    elif fire_mode == "once_per_episode":
        if condition_met:
            if state.armed and not in_cooldown:
                should_fire = True
                new_armed = False
                new_fired_count = state.fired_count + 1
                new_last_fired = ts
            else:
                should_fire = False
                new_armed = False
                new_fired_count = state.fired_count
                new_last_fired = state.last_fired_at
        else:
            should_fire = False
            new_armed = True  # Re-arm on false
            new_fired_count = state.fired_count
            new_last_fired = state.last_fired_at

    elif fire_mode == "once_ever":
        if condition_met and state.fired_count == 0 and not in_cooldown:
            should_fire = True
            new_armed = False
            new_fired_count = 1
            new_last_fired = ts
        else:
            should_fire = False
            new_armed = False
            new_fired_count = state.fired_count
            new_last_fired = state.last_fired_at

    alert_text = generate_alert_text(rule, norm_history, agg_value) if should_fire else None

    decision = EvaluationDecision(
        fired=should_fire,
        rule_id=rule.id,
        timestamp=ts if should_fire else None,
        alert_text=alert_text,
        value=agg_value,
        condition_met=condition_met,
    )

    new_state = RuleState(
        baseline=current_baseline,
        armed=new_armed,
        last_fired_at=new_last_fired,
        fired_count=new_fired_count,
        monitoring_started_at=current_monitoring_started_at,
    )

    return decision, new_state
