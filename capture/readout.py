import sqlite3
from dataclasses import dataclass

from capture.slots import ScanIdentity, find_slot_scan
from quality.plan import SamplingPlan, validate_name


class UnknownScan(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ReadoutHeader:
    identity: ScanIdentity
    plan: SamplingPlan
    unit: str


@dataclass(frozen=True, slots=True)
class ReadoutRow:
    position: int
    value: int
    identity: ScanIdentity
    unit: str


@dataclass(frozen=True, slots=True)
class ScanReadout:
    header: ReadoutHeader
    rows: tuple[ReadoutRow, ...]


def read_scan(db: sqlite3.Connection, scan_id: str) -> ScanReadout:
    validate_name(scan_id, 'scan id')
    scan = find_slot_scan(db, scan_id)
    if scan is None:
        raise UnknownScan(f'no current device original for {scan_id}')
    return ScanReadout(
        ReadoutHeader(scan.identity, scan.plan, scan.plan.unit),
        tuple(
            ReadoutRow(position, value, scan.identity, scan.plan.unit)
            for position, value in enumerate(scan.readings, 1)
        ),
    )
