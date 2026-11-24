import sqlite3
from collections.abc import Sequence

from capture.slots import (
    ScanIdentity,
    SlotScan,
    find_current_version,
    find_sealed_version,
    find_slot_scan,
    replace_slot_readout,
    reserve_revisable_slot,
    reserve_slot,
    validate_identity,
)
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


def record_revisable(
    db: sqlite3.Connection,
    scan_id: str,
    device_id: str,
    trigger: int,
    plan: SamplingPlan,
    readings: Sequence[int],
) -> tuple[int, int]:
    """首次登记一个可重扫原件，返回 (version, trigger)。"""
    identity = ScanIdentity(scan_id, device_id, trigger)
    validate_identity(identity)
    validate_plan(plan)
    if identity.device_id != plan.source:
        raise ValueError('trigger source does not match its sampling plan')
    values = validate_readings(readings, points=plan.points)
    version = reserve_revisable_slot(db, SlotScan(identity, plan, values))
    return version, trigger


def rescan(
    db: sqlite3.Connection,
    scan_id: str,
    trigger: int,
    readings: Sequence[int],
) -> tuple[int, int]:
    """在原槽离线重扫同一原件，只改写当前实扫；返回新的 (version, trigger)。"""
    if type(trigger) is not int or trigger < 0:
        raise ValueError('trigger must be a nonnegative integer')
    scan = find_slot_scan(db, scan_id)
    if scan is None:
        raise ValueError(f'no occupied device original for {scan_id}')
    current = find_current_version(db, scan_id)
    if current is None:
        raise ValueError(f'{scan_id} is not a revisable original')
    if find_sealed_version(db, scan_id) is not None:
        raise ValueError(f'{scan_id} is sealed and cannot be rescanned')
    values = validate_readings(readings, points=scan.plan.points)
    version = replace_slot_readout(db, scan_id, current, trigger, values)
    return version, trigger
