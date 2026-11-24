import sqlite3
from dataclasses import dataclass

from quality.plan import SamplingPlan, validate_name
from quality.readings import decode_integer, encode_integer


class SlotBusy(RuntimeError):
    pass


class CaptureIdentityConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScanIdentity:
    scan_id: str
    device_id: str
    trigger: int


@dataclass(frozen=True, slots=True)
class SlotScan:
    identity: ScanIdentity
    plan: SamplingPlan
    readings: tuple[int, ...]


def validate_identity(identity: ScanIdentity) -> None:
    if not isinstance(identity, ScanIdentity):
        raise ValueError('a scan identity is required')
    validate_name(identity.scan_id, 'scan id')
    validate_name(identity.device_id, 'device id')
    if type(identity.trigger) is not int or identity.trigger < 0:
        raise ValueError('trigger must be a nonnegative integer')


def find_capture_identity(db: sqlite3.Connection, scan_id: str) -> ScanIdentity | None:
    row = db.execute(
        'SELECT device_id, trigger_code FROM capture_identities WHERE scan_id = ?',
        (scan_id,),
    ).fetchone()
    if row is None:
        return None
    return ScanIdentity(scan_id, row[0], decode_integer(row[1]))


def reserve_slot(db: sqlite3.Connection, scan: SlotScan) -> SlotScan:
    identity = scan.identity
    plan = scan.plan
    trigger_code = encode_integer(identity.trigger)
    with db:
        db.execute('BEGIN IMMEDIATE')
        occupied = db.execute('SELECT scan_id FROM device_slot WHERE slot_no = 1').fetchone()
        if occupied is not None:
            raise SlotBusy(f'slot occupied by {occupied[0]}')
        reused = db.execute(
            '''SELECT scan_id FROM capture_identities
               WHERE scan_id = ? OR (device_id = ? AND trigger_code = ?)''',
            (identity.scan_id, identity.device_id, trigger_code),
        ).fetchone()
        if reused is not None:
            raise CaptureIdentityConflict(f'capture identity already used by {reused[0]}')
        db.execute(
            'INSERT INTO capture_identities (scan_id, device_id, trigger_code) VALUES (?, ?, ?)',
            (identity.scan_id, identity.device_id, trigger_code),
        )
        db.execute(
            '''INSERT INTO device_slot (slot_no, scan_id, plan_id, points, unit, source)
               VALUES (1, ?, ?, ?, ?, ?)''',
            (identity.scan_id, plan.plan_id, plan.points, plan.unit, plan.source),
        )
        db.executemany(
            'INSERT INTO slot_readings (slot_no, position, value_code) VALUES (1, ?, ?)',
            ((position, encode_integer(value)) for position, value in enumerate(scan.readings, 1)),
        )
    return scan


def find_slot_scan(db: sqlite3.Connection, scan_id: str) -> SlotScan | None:
    rows = db.execute(
        '''SELECT i.device_id, i.trigger_code, s.plan_id, s.points, s.unit, s.source,
                  r.position, r.value_code
           FROM device_slot AS s
           JOIN capture_identities AS i ON i.scan_id = s.scan_id
           LEFT JOIN slot_readings AS r ON r.slot_no = s.slot_no
           WHERE s.scan_id = ? ORDER BY r.position''',
        (scan_id,),
    ).fetchall()
    if not rows:
        return None
    device_id, trigger_code, plan_id, points, unit, source, _, _ = rows[0]
    if len(rows) != points or any(row[6] != position for position, row in enumerate(rows, 1)):
        raise ValueError('device slot contains an incomplete scan')
    return SlotScan(
        ScanIdentity(scan_id, device_id, decode_integer(trigger_code)),
        SamplingPlan(plan_id, points, unit, source),
        tuple(decode_integer(row[7]) for row in rows),
    )
