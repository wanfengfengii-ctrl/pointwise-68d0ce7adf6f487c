import sqlite3
from dataclasses import dataclass

from capture.slots import ScanIdentity
from quality.readings import decode_integer, encode_integer


class RequestParameterConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RevisionApproval:
    request_id: str
    scan_id: str
    version: int


_REVISION_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS revision_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    scan_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    UNIQUE (request_id, scan_id, version_no)
);
CREATE TABLE IF NOT EXISTS revision_decisions (
    scan_id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    device_id TEXT NOT NULL,
    trigger_code TEXT NOT NULL,
    result_id TEXT
);
CREATE TABLE IF NOT EXISTS revision_progress (
    request_id TEXT PRIMARY KEY NOT NULL REFERENCES revision_decisions (request_id),
    result_id TEXT NOT NULL
);
"""


def initialize_revision_journal(db: sqlite3.Connection) -> None:
    db.executescript(_REVISION_JOURNAL_SCHEMA)


def load_revision_request(
    db: sqlite3.Connection, request_id: str
) -> RevisionApproval | None:
    row = db.execute(
        'SELECT scan_id, version_no FROM revision_requests WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    return None if row is None else RevisionApproval(request_id, row[0], row[1])


def bind_revision_request(
    db: sqlite3.Connection, approval: RevisionApproval
) -> RevisionApproval:
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute(
            '''INSERT INTO revision_requests (request_id, scan_id, version_no)
               VALUES (?, ?, ?) ON CONFLICT (request_id) DO NOTHING''',
            (approval.request_id, approval.scan_id, approval.version),
        )
        stored = load_revision_request(db, approval.request_id)
        if stored is None:
            raise ValueError(f'approval request {approval.request_id} was not registered')
        if stored != approval:
            raise RequestParameterConflict(
                f'parameters differ for request {approval.request_id}'
            )
    return approval


def find_revision_decision(
    db: sqlite3.Connection, scan_id: str
):
    row = db.execute(
        '''SELECT request_id, version_no, device_id, trigger_code, result_id
           FROM revision_decisions WHERE scan_id = ?''',
        (scan_id,),
    ).fetchone()
    if row is None:
        return None
    request_id, version, device_id, trigger_code, result_id = row
    return {
        'request': RevisionApproval(request_id, scan_id, version),
        'identity': ScanIdentity(scan_id, device_id, decode_integer(trigger_code)),
        'result_id': result_id,
    }


def record_revision_decision(
    db: sqlite3.Connection,
    approval: RevisionApproval,
    identity: ScanIdentity,
    result_id: str | None,
) -> None:
    if not db.in_transaction:
        raise ValueError('revision decision must be recorded in the approval transaction')
    db.execute(
        '''INSERT INTO revision_decisions
           (scan_id, request_id, version_no, device_id, trigger_code, result_id)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (
            identity.scan_id,
            approval.request_id,
            approval.version,
            identity.device_id,
            encode_integer(identity.trigger),
            result_id,
        ),
    )


def mark_revision_result(db: sqlite3.Connection, scan_id: str, result_id: str) -> None:
    if not db.in_transaction:
        raise ValueError('revision result must be marked in the approval transaction')
    db.execute(
        'UPDATE revision_decisions SET result_id = ? WHERE scan_id = ?',
        (result_id, scan_id),
    )


def load_revision_progress(
    db: sqlite3.Connection, request_id: str
) -> str | None:
    row = db.execute(
        'SELECT result_id FROM revision_progress WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    return None if row is None else row[0]


def save_revision_progress(
    db: sqlite3.Connection, request_id: str, result_id: str
) -> None:
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute(
            '''INSERT INTO revision_progress (request_id, result_id) VALUES (?, ?)
               ON CONFLICT (request_id) DO NOTHING''',
            (request_id, result_id),
        )
        if load_revision_progress(db, request_id) != result_id:
            raise ValueError('confirmed revision progress conflicts with the original approval')
