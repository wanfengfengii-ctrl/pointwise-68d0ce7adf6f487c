import sqlite3
from dataclasses import dataclass
from os import PathLike

from archive.payload import initialize_payload_store
from disposition.journal import initialize_journal
from disposition.revision_journal import initialize_revision_journal
from intake.review_staging import initialize_review_staging
from intake.staging import initialize_staging


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    result_id: str
    scan_id: str


_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_catalog (
    result_id TEXT PRIMARY KEY NOT NULL,
    scan_id TEXT NOT NULL UNIQUE
);
"""


def _migrate_catalog(db: sqlite3.Connection) -> None:
    # 旧库的 archive_catalog 以 terminal_decisions 为外键；重扫核准不走普通终局，
    # 目录只保留同一成果号/原件的唯一性约束，正式资格由发布事务在代码侧保证。
    # archive_payloads 旧表外键指向目录表，重建目录时须一并重建载荷表。
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'archive_catalog'"
    ).fetchone()
    if row is None or 'REFERENCES' not in row[0].upper():
        return
    db.execute('PRAGMA foreign_keys = OFF')
    db.execute('ALTER TABLE archive_catalog RENAME TO archive_catalog_legacy')
    db.execute('ALTER TABLE archive_payloads RENAME TO archive_payloads_legacy')
    db.execute(
        '''CREATE TABLE archive_catalog (
               result_id TEXT PRIMARY KEY NOT NULL,
               scan_id TEXT NOT NULL UNIQUE
           )'''
    )
    db.execute(
        '''CREATE TABLE archive_payloads (
               result_id TEXT PRIMARY KEY NOT NULL
                   REFERENCES archive_catalog (result_id),
               scan_id TEXT NOT NULL,
               device_id TEXT NOT NULL,
               trigger_code TEXT NOT NULL,
               unit TEXT NOT NULL,
               readings_json TEXT NOT NULL,
               spread_code TEXT NOT NULL,
               version_no INTEGER
           )'''
    )
    db.execute(
        'INSERT INTO archive_catalog (result_id, scan_id) '
        'SELECT result_id, scan_id FROM archive_catalog_legacy'
    )
    db.execute(
        '''INSERT INTO archive_payloads
           (result_id, scan_id, device_id, trigger_code, unit, readings_json,
            spread_code, version_no)
           SELECT result_id, scan_id, device_id, trigger_code, unit, readings_json,
                  spread_code, NULL FROM archive_payloads_legacy'''
    )
    db.execute('DROP TABLE archive_payloads_legacy')
    db.execute('DROP TABLE archive_catalog_legacy')
    db.execute('PRAGMA foreign_keys = ON')
    if not db.execute('PRAGMA foreign_key_check').fetchall() == []:
        raise ValueError('archive catalog migration failed the foreign key check')


def initialize_catalog(db: sqlite3.Connection) -> None:
    db.executescript(_CATALOG_SCHEMA)
    _migrate_catalog(db)


def initialize_archive_store(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        raise ValueError('initialize the archive store on an idle connection')
    db.execute('PRAGMA foreign_keys = ON')
    initialize_staging(db)
    initialize_review_staging(db)
    initialize_journal(db)
    initialize_revision_journal(db)
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
