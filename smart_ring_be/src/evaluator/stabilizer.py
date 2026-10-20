from dataclasses import dataclass
from typing import Any, List, Optional, Tuple, Union

from src.evaluator.models import Observation, StableObservation


@dataclass(frozen=True)
class StabilizerState:
    committed_count: Optional[int] = None
    candidate_count: Optional[int] = None
    candidate_streak: int = 0
    baseline: Optional[int] = None


def stabilizer_step(
    state: StabilizerState,
    obs: Observation,
    n: int = 2,
) -> Tuple[Optional[StableObservation], StabilizerState]:
    """Pure single-step stabilizer transition.

    Commit a new count only after it holds for N consecutive observations.
    The first stable count after monitoring starts is the BASELINE.
    """
    if n < 1:
        raise ValueError("Stabilizer consecutive count threshold N must be >= 1.")

    count = obs.package_count

    # Phase 1: Baseline not yet established
    if state.committed_count is None:
        new_streak = (state.candidate_streak + 1) if state.candidate_count == count else 1

        if new_streak >= n:
            new_state = StabilizerState(
                committed_count=count,
                candidate_count=None,
                candidate_streak=0,
                baseline=count,
            )
            return StableObservation(timestamp=obs.timestamp, package_count=count), new_state
        else:
            new_state = StabilizerState(
                committed_count=None,
                candidate_count=count,
                candidate_streak=new_streak,
                baseline=None,
            )
            return None, new_state

    # Phase 2: Baseline established
    if count == state.committed_count:
        new_state = StabilizerState(
            committed_count=state.committed_count,
            candidate_count=None,
            candidate_streak=0,
            baseline=state.baseline,
        )
        return StableObservation(timestamp=obs.timestamp, package_count=state.committed_count), new_state

    # Candidate differs from currently committed count
    new_streak = (state.candidate_streak + 1) if state.candidate_count == count else 1

    if new_streak >= n:
        new_state = StabilizerState(
            committed_count=count,
            candidate_count=None,
            candidate_streak=0,
            baseline=state.baseline,
        )
        return StableObservation(timestamp=obs.timestamp, package_count=count), new_state
    else:
        new_state = StabilizerState(
            committed_count=state.committed_count,
            candidate_count=count,
            candidate_streak=new_streak,
            baseline=state.baseline,
        )
        return StableObservation(timestamp=obs.timestamp, package_count=state.committed_count), new_state


def stabilize(
    observations: List[Union[Observation, dict]],
    n: int = 2,
) -> List[StableObservation]:
    """Purity-preserving stabilizer that processes a sequence of observations."""
    state = StabilizerState()
    stable_results: List[StableObservation] = []

    for item in observations:
        if isinstance(item, dict):
            obs = Observation(timestamp=item["timestamp"], package_count=item["package_count"])
        else:
            obs = item
        stable_obs, state = stabilizer_step(state, obs, n=n)
        if stable_obs is not None:
            stable_results.append(stable_obs)

    return stable_results
