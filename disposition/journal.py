import sqlite3
from dataclasses import dataclass

from capture.slots import ScanIdentity
from quality.readings import decode_integer, encode_integer


class RequestParameterConflict(ValueError):
    pass


class UnknownRequest(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class DispositionRequest:
    request_id: str
    scan_id: str
    operation: str


@dataclass(frozen=True, slots=True)
class TerminalDecision:
    request: DispositionRequest
    identity: ScanIdentity


@dataclass(frozen=True, slots=True)
class DispositionProgress:
    result_id: str | None


_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS disposition_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    scan_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('file', 'withdraw')),
    UNIQUE (request_id, scan_id)
);
CREATE TABLE IF NOT EXISTS terminal_decisions (
    scan_id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    device_id TEXT NOT NULL,
    trigger_code TEXT NOT NULL,
    FOREIGN KEY (request_id, scan_id) REFERENCES disposition_requests (request_id, scan_id)
);
CREATE TABLE IF NOT EXISTS disposition_progress (
    request_id TEXT PRIMARY KEY NOT NULL REFERENCES terminal_decisions (request_id),
    result_id TEXT
);
"""


def initialize_journal(db: sqlite3.Connection) -> None:
    db.executescript(_JOURNAL_SCHEMA)


def load_request(db: sqlite3.Connection, request_id: str) -> DispositionRequest | None:
    row = db.execute(
        'SELECT scan_id, operation FROM disposition_requests WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    return None if row is None else DispositionRequest(request_id, *row)


def require_matching_request(
    stored: DispositionRequest | None, requested: DispositionRequest,
) -> DispositionRequest:
    if stored is None:
        raise UnknownRequest(f'unknown request {requested.request_id}')
    if stored != requested:
        raise RequestParameterConflict(f'parameters differ for request {requested.request_id}')
    return stored


def bind_request(db: sqlite3.Connection, request: DispositionRequest) -> DispositionRequest:
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute(
            '''INSERT INTO disposition_requests (request_id, scan_id, operation) VALUES (?, ?, ?)
               ON CONFLICT (request_id) DO NOTHING''',
            (request.request_id, request.scan_id, request.operation),
        )
        return require_matching_request(load_request(db, request.request_id), request)


def find_terminal(db: sqlite3.Connection, scan_id: str) -> TerminalDecision | None:
    row = db.execute(
        '''SELECT t.request_id, r.operation, t.device_id, t.trigger_code
           FROM terminal_decisions AS t
           JOIN disposition_requests AS r ON r.request_id = t.request_id
           WHERE t.scan_id = ?''',
        (scan_id,),
    ).fetchone()
    if row is None:
        return None
    request_id, operation, device_id, trigger_code = row
    return TerminalDecision(
        DispositionRequest(request_id, scan_id, operation),
        ScanIdentity(scan_id, device_id, decode_integer(trigger_code)),
    )


def record_terminal(db: sqlite3.Connection, decision: TerminalDecision) -> None:
    if not db.in_transaction:
        raise ValueError('terminal must be recorded in the disposition transaction')
    identity = decision.identity
    db.execute(
        '''INSERT INTO terminal_decisions (scan_id, request_id, device_id, trigger_code)
           VALUES (?, ?, ?, ?)''',
        (identity.scan_id, decision.request.request_id, identity.device_id, encode_integer(identity.trigger)),
    )


def load_progress(db: sqlite3.Connection, request_id: str) -> DispositionProgress | None:
    row = db.execute(
        'SELECT result_id FROM disposition_progress WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    return None if row is None else DispositionProgress(row[0])


def save_progress(
    db: sqlite3.Connection, request_id: str, progress: DispositionProgress,
) -> DispositionProgress:
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute(
            '''INSERT INTO disposition_progress (request_id, result_id) VALUES (?, ?)
               ON CONFLICT (request_id) DO NOTHING''',
            (request_id, progress.result_id),
        )
        if load_progress(db, request_id) != progress:
            raise ValueError('confirmed progress conflicts with the original disposition')
    return progress
