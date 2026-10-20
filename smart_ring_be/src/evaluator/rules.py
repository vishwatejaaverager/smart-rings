from typing import List
from src.evaluator.models import Rule

# Hardcoded rules from specification, validated by Pydantic
RULES_CONFIG = [
    {
        "id": "disappears",
        "source": "package_count",
        "derive": {"type": "delta", "keep": "negative"},
        "aggregate": {"fn": "latest", "window": {"type": "always"}},
        "compare": {"op": "<", "value": 0},
        "alert": {"fire": "every_time", "cooldown_seconds": 0},
    },
    {
        "id": "three_new_arrivals",
        "source": "package_count",
        "derive": {"type": "delta", "keep": "positive"},
        "aggregate": {"fn": "sum", "window": {"type": "since_monitoring_started"}},
        "compare": {"op": ">=", "value": 3},
        "alert": {"fire": "once_ever", "cooldown_seconds": 0},
    },
    {
        "id": "fewer_than_two",
        "source": "package_count",
        "derive": {"type": "identity"},
        "aggregate": {"fn": "latest", "window": {"type": "always"}},
        "compare": {"op": "<", "value": 2},
        "alert": {"fire": "once_per_episode", "cooldown_seconds": 0},
    },
]

# Validate with Pydantic model
HARDCODED_RULES: List[Rule] = [Rule.model_validate(r) for r in RULES_CONFIG]


def get_hardcoded_rules() -> List[Rule]:
    """Returns deep copies of the validated hardcoded rules."""
    return [r.model_copy(deep=True) for r in HARDCODED_RULES]
