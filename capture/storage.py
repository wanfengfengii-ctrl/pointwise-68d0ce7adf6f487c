import sqlite3
from os import PathLike


_DEVICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS capture_identities (
    scan_id TEXT PRIMARY KEY NOT NULL,
    device_id TEXT NOT NULL,
    trigger_code TEXT NOT NULL,
    UNIQUE (device_id, trigger_code)
);
CREATE TABLE IF NOT EXISTS device_slot (
    slot_no INTEGER PRIMARY KEY CHECK (slot_no = 1),
    scan_id TEXT NOT NULL UNIQUE REFERENCES capture_identities (scan_id),
    plan_id TEXT NOT NULL,
    points INTEGER NOT NULL CHECK (points > 0),
    unit TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slot_readings (
    slot_no INTEGER NOT NULL REFERENCES device_slot (slot_no) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position > 0),
    value_code TEXT NOT NULL,
    PRIMARY KEY (slot_no, position)
);
CREATE TABLE IF NOT EXISTS revisable_scans (
    scan_id TEXT PRIMARY KEY NOT NULL REFERENCES capture_identities (scan_id),
    current_version INTEGER NOT NULL CHECK (current_version > 0)
);
CREATE TABLE IF NOT EXISTS scan_versions (
    scan_id TEXT NOT NULL REFERENCES capture_identities (scan_id),
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    trigger_code TEXT NOT NULL,
    PRIMARY KEY (scan_id, version_no),
    UNIQUE (scan_id, trigger_code)
);
CREATE TABLE IF NOT EXISTS device_seals (
    scan_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    PRIMARY KEY (scan_id, request_id),
    UNIQUE (scan_id, version_no)
);
CREATE TABLE IF NOT EXISTS device_releases (
    scan_id TEXT PRIMARY KEY NOT NULL REFERENCES capture_identities (scan_id),
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('file', 'withdraw', 'approve'))
);
"""


def _migrate_device_store(db: sqlite3.Connection) -> None:
    # Older stores only accepted file/withdraw releases. Rebuild the table so
    # the approval release of a sealed revision can use the same durable fact.
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'device_releases'"
    ).fetchone()
    if row is not None and "'approve'" not in row[0]:
        db.execute('ALTER TABLE device_releases RENAME TO device_releases_legacy')
        db.execute(
            '''CREATE TABLE device_releases (
                   scan_id TEXT PRIMARY KEY NOT NULL REFERENCES capture_identities (scan_id),
                   request_id TEXT NOT NULL,
                   operation TEXT NOT NULL
                       CHECK (operation IN ('file', 'withdraw', 'approve'))
               )'''
        )
        db.execute(
            'INSERT INTO device_releases (scan_id, request_id, operation) '
            'SELECT scan_id, request_id, operation FROM device_releases_legacy'
        )
        db.execute('DROP TABLE device_releases_legacy')


def initialize_device_store(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        raise ValueError('initialize the device store on an idle connection')
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript(_DEVICE_SCHEMA)
    _migrate_device_store(db)


def open_device_store(path: str | PathLike[str]) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_device_store(db)
    except BaseException:
        db.close()
        raise
    return db
