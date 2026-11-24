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
CREATE TABLE IF NOT EXISTS device_releases (
    scan_id TEXT PRIMARY KEY NOT NULL REFERENCES capture_identities (scan_id),
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('file', 'withdraw'))
);
"""


def initialize_device_store(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        raise ValueError('initialize the device store on an idle connection')
    db.execute('PRAGMA foreign_keys = ON')
    db.executescript(_DEVICE_SCHEMA)


def open_device_store(path: str | PathLike[str]) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_device_store(db)
    except BaseException:
        db.close()
        raise
    return db
