from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SamplingPlan:
    plan_id: str
    points: int
    unit: str
    source: str


def validate_name(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{field} must be a nonblank name')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{field} must be valid UTF-8 text') from exc


def validate_plan(plan: SamplingPlan) -> SamplingPlan:
    if not isinstance(plan, SamplingPlan):
        raise ValueError('a SamplingPlan is required')
    validate_name(plan.plan_id, 'plan id')
    validate_name(plan.unit, 'unit')
    validate_name(plan.source, 'source')
    if type(plan.points) is not int or plan.points <= 0:
        raise ValueError('plan points must be a positive integer')
    return plan
