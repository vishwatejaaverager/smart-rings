from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


class DeriveConfig(BaseModel):
    type: Literal["delta", "identity"]
    keep: Optional[Literal["all", "positive", "negative"]] = "all"


class WindowConfig(BaseModel):
    type: Literal["always", "since_monitoring_started"]


class AggregateConfig(BaseModel):
    fn: Literal["latest", "sum", "count", "max", "min"]
    window: WindowConfig


class CompareConfig(BaseModel):
    op: Literal["<", "<=", ">", ">=", "==", "!="]
    value: Union[int, float]


class AlertConfig(BaseModel):
    fire: Literal["every_time", "once_per_episode", "once_ever"]
    cooldown_seconds: float = Field(default=0.0, ge=0.0)


class Rule(BaseModel):
    id: str = Field(min_length=1)
    source: Literal["package_count", "person", "car"] = "package_count"
    vlm_query: Optional[str] = None  # Targeted question for Qwen (e.g. "Is this person wearing a blue shirt? Answer YES or NO.")
    expected_answer: Optional[str] = "YES"
    derive: Optional[DeriveConfig] = None
    aggregate: Optional[AggregateConfig] = None
    compare: Optional[CompareConfig] = None
    alert: AlertConfig

    @model_validator(mode="after")
    def validate_evaluation_fields(self):
        if self.source == "package_count":
            if self.derive is None or self.aggregate is None or self.compare is None:
                raise ValueError("Package rules require derive, aggregate, and compare configurations")
        else:
            if not self.vlm_query or not self.vlm_query.strip():
                raise ValueError("Visual rules require a nonempty vlm_query")
            if self.expected_answer != "YES":
                raise ValueError("Visual rules must express their matching condition as expected_answer=YES")
        return self


@dataclass(frozen=True)
class Observation:
    timestamp: Union[float, int, str]
    package_count: int


@dataclass(frozen=True)
class StableObservation:
    timestamp: Union[float, int, str]
    package_count: int


@dataclass(frozen=True)
class ChangeEvent:
    timestamp: Union[float, int, str]
    prev: int
    curr: int
    delta: int


@dataclass(frozen=True)
class RuleState:
    baseline: Optional[int] = None
    armed: bool = True
    last_fired_at: Optional[Union[float, int, str]] = None
    fired_count: int = 0
    monitoring_started_at: Optional[Union[float, int, str]] = None


@dataclass(frozen=True)
class EvaluationDecision:
    fired: bool
    rule_id: str
    timestamp: Optional[Union[float, int, str]] = None
    alert_text: Optional[str] = None
    value: Optional[Union[int, float]] = None
    condition_met: bool = False
