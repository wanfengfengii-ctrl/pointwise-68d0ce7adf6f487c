import sqlite3

from intake.schema import StagedScan


class ReviewVersionMismatch(ValueError):
    pass


_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_staged_headers (
    scan_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    device_id TEXT NOT NULL,
    trigger_code TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    points INTEGER NOT NULL CHECK (points > 0),
    unit TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (scan_id, version_no)
);
CREATE TABLE IF NOT EXISTS review_staged_readings (
    scan_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    position INTEGER NOT NULL CHECK (position > 0),
    value_code TEXT NOT NULL,
    PRIMARY KEY (scan_id, version_no, position)
);
"""


def initialize_review_staging(db: sqlite3.Connection) -> None:
    db.executescript(_REVIEW_SCHEMA)


def load_review_staged_scan(
    db: sqlite3.Connection, scan_id: str, version: int
) -> StagedScan | None:
    rows = db.execute(
        '''SELECT h.scan_id, h.device_id, h.trigger_code, h.plan_id, h.points, h.unit,
                  h.source, r.position, r.value_code
           FROM review_staged_headers AS h
           LEFT JOIN review_staged_readings AS r
               ON r.scan_id = h.scan_id AND r.version_no = h.version_no
           WHERE h.scan_id = ? AND h.version_no = ?
           ORDER BY r.position''',
        (scan_id, version),
    ).fetchall()
    if not rows:
        return None
    from intake.codec import decode_staged_scan

    return decode_staged_scan(rows[0][:7], tuple(row[7:] for row in rows))


def store_review_staged_scan(
    db: sqlite3.Connection, version: int, scan: StagedScan
) -> StagedScan:
    from intake.codec import encode_staged_scan

    scan_id = scan.identity.scan_id
    header, rows = encode_staged_scan(scan)
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = load_review_staged_scan(db, scan_id, version)
        if existing is not None:
            if existing != scan:
                raise ReviewVersionMismatch(
                    f'contradictory review staging for {scan_id} version {version}'
                )
            return existing
        db.execute(
            '''INSERT INTO review_staged_headers
               (scan_id, version_no, device_id, trigger_code, plan_id, points, unit, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (scan_id, version, *header[1:]),
        )
        db.executemany(
            '''INSERT INTO review_staged_readings
               (scan_id, version_no, position, value_code) VALUES (?, ?, ?, ?)''',
            ((scan_id, version, position, value) for position, value in rows),
        )
    return scan
