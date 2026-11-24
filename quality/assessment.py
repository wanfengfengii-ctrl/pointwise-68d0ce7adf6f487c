from collections.abc import Sequence
from dataclasses import dataclass

from quality.plan import SamplingPlan, validate_plan
from quality.range import calculate_range


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    plan_id: str
    points: int
    unit: str
    spread: int


def assess_scan(plan: SamplingPlan, readings: Sequence[int]) -> QualityAssessment:
    validate_plan(plan)
    spread = calculate_range(readings)
    if len(readings) != plan.points:
        raise ValueError('scan does not match the plan point count')
    return QualityAssessment(plan.plan_id, plan.points, plan.unit, spread)
