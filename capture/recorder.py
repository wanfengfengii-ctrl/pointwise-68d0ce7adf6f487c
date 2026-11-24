import sqlite3
from collections.abc import Sequence

from capture.slots import ScanIdentity, SlotScan, reserve_slot, validate_identity
from quality.plan import SamplingPlan, validate_plan
from quality.readings import validate_readings


def record(
    db: sqlite3.Connection,
    scan_id: str,
    device_id: str,
    trigger: int,
    plan: SamplingPlan,
    readings: Sequence[int],
) -> SlotScan:
    identity = ScanIdentity(scan_id, device_id, trigger)
    validate_identity(identity)
    validate_plan(plan)
    if identity.device_id != plan.source:
        raise ValueError('trigger source does not match its sampling plan')
    values = validate_readings(readings, points=plan.points)
    return reserve_slot(db, SlotScan(identity, plan, values))
