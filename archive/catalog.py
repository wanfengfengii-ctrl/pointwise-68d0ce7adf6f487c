import sqlite3
from dataclasses import dataclass
from os import PathLike

from archive.payload import initialize_payload_store
from disposition.journal import initialize_journal
from intake.staging import initialize_staging


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    result_id: str
    scan_id: str


_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_catalog (
    result_id TEXT PRIMARY KEY NOT NULL,
    scan_id TEXT NOT NULL UNIQUE REFERENCES terminal_decisions (scan_id)
);
"""


def initialize_catalog(db: sqlite3.Connection) -> None:
    db.executescript(_CATALOG_SCHEMA)


def initialize_archive_store(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        raise ValueError('initialize the archive store on an idle connection')
    db.execute('PRAGMA foreign_keys = ON')
    initialize_staging(db)
    initialize_journal(db)
    initialize_catalog(db)
    initialize_payload_store(db)


def open_archive_store(path: str | PathLike[str]) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        initialize_archive_store(db)
    except BaseException:
        db.close()
        raise
    return db


def register_result(db: sqlite3.Connection, scan_id: str) -> ArchiveEntry:
    if not db.in_transaction:
        raise ValueError('catalogue registration requires a publication transaction')
    result_id = f'LS:{scan_id}'
    db.execute(
        'INSERT INTO archive_catalog (result_id, scan_id) VALUES (?, ?)',
        (result_id, scan_id),
    )
    return ArchiveEntry(result_id, scan_id)


def find_result(db: sqlite3.Connection, result_id: str) -> ArchiveEntry | None:
    row = db.execute(
        'SELECT scan_id FROM archive_catalog WHERE result_id = ?',
        (result_id,),
    ).fetchone()
    return None if row is None else ArchiveEntry(result_id, row[0])


def find_result_for_scan(db: sqlite3.Connection, scan_id: str) -> ArchiveEntry | None:
    row = db.execute(
        'SELECT result_id FROM archive_catalog WHERE scan_id = ?',
        (scan_id,),
    ).fetchone()
    return None if row is None else ArchiveEntry(row[0], scan_id)
