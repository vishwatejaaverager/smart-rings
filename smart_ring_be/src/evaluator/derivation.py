from typing import List, Union
from src.evaluator.models import ChangeEvent, StableObservation


def derive_change_events(
    stable_observations: List[Union[StableObservation, dict]],
) -> List[ChangeEvent]:
    """Pure derivation: from stable counts, emit change events {timestamp, prev, curr, delta},
    only when the stable count actually changes."""
    events: List[ChangeEvent] = []
    if len(stable_observations) < 2:
        return events

    for i in range(1, len(stable_observations)):
        prev_item = stable_observations[i - 1]
        curr_item = stable_observations[i]

        prev_count = (
            prev_item["package_count"]
            if isinstance(prev_item, dict)
            else prev_item.package_count
        )
        curr_count = (
            curr_item["package_count"]
            if isinstance(curr_item, dict)
            else curr_item.package_count
        )
        curr_ts = (
            curr_item["timestamp"]
            if isinstance(curr_item, dict)
            else curr_item.timestamp
        )

        if curr_count != prev_count:
            events.append(
                ChangeEvent(
                    timestamp=curr_ts,
                    prev=prev_count,
                    curr=curr_count,
                    delta=curr_count - prev_count,
                )
            )

    return events
