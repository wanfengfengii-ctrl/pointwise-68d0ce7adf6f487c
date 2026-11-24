import sqlite3

from intake.codec import decode_staged_scan, encode_staged_scan
from intake.schema import StagedScan


class StagingConflict(ValueError):
    pass


_STAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS staged_headers (
    scan_id TEXT PRIMARY KEY NOT NULL,
    device_id TEXT NOT NULL,
    trigger_code TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    points INTEGER NOT NULL CHECK (points > 0),
    unit TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staged_readings (
    scan_id TEXT NOT NULL REFERENCES staged_headers (scan_id),
    position INTEGER NOT NULL CHECK (position > 0),
    value_code TEXT NOT NULL,
    PRIMARY KEY (scan_id, position)
);
"""


def initialize_staging(db: sqlite3.Connection) -> None:
    db.executescript(_STAGING_SCHEMA)


def load_staged_scan(db: sqlite3.Connection, scan_id: str) -> StagedScan | None:
    rows = db.execute(
        '''SELECT h.scan_id, h.device_id, h.trigger_code, h.plan_id, h.points, h.unit,
                  h.source, r.position, r.value_code
           FROM staged_headers AS h
           LEFT JOIN staged_readings AS r ON r.scan_id = h.scan_id
           WHERE h.scan_id = ? ORDER BY r.position''',
        (scan_id,),
    ).fetchall()
    if not rows:
        return None
    return decode_staged_scan(rows[0][:7], tuple(row[7:] for row in rows))


def store_staged_scan(db: sqlite3.Connection, scan: StagedScan) -> StagedScan:
    scan_id = scan.identity.scan_id
    header, rows = encode_staged_scan(scan)
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = load_staged_scan(db, scan_id)
        if existing is not None:
            if existing != scan:
                raise StagingConflict(f'contradictory staging for {scan_id}')
            return existing
        db.execute(
            '''INSERT INTO staged_headers (scan_id, device_id, trigger_code, plan_id, points, unit, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            header,
        )
        db.executemany(
            'INSERT INTO staged_readings (scan_id, position, value_code) VALUES (?, ?, ?)',
            ((scan_id, position, value) for position, value in rows),
        )
    return scan
