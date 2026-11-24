import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from capture.readout import ScanReadout, read_scan
from capture.slots import ScanIdentity, find_capture_identity, validate_identity
from quality.plan import validate_name
from quality.readings import decode_integer


class PortUnavailable(OSError):
    pass


class UnauthorizedRelease(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseBasis:
    identity: ScanIdentity
    request_id: str
    operation: str
    result_id: str | None


@dataclass(frozen=True, slots=True)
class ReleaseFact:
    identity: ScanIdentity
    request_id: str
    operation: str


def find_release_fact(db: sqlite3.Connection, scan_id: str) -> ReleaseFact | None:
    row = db.execute(
        '''SELECT i.device_id, i.trigger_code, r.request_id, r.operation
           FROM device_releases AS r
           JOIN capture_identities AS i ON i.scan_id = r.scan_id
           WHERE r.scan_id = ?''',
        (scan_id,),
    ).fetchone()
    if row is None:
        return None
    device_id, trigger_code, request_id, operation = row
    identity = ScanIdentity(scan_id, device_id, decode_integer(trigger_code))
    return ReleaseFact(identity, request_id, operation)


def _confirm_matching_release(existing: ReleaseFact, expected: ReleaseFact) -> ReleaseFact:
    if existing != expected:
        raise UnauthorizedRelease('existing release belongs to another disposition')
    return existing


def release_scan(
    db: sqlite3.Connection,
    identity: ScanIdentity,
    request_id: str,
    operation: str,
    lookup_basis: Callable[[str], ReleaseBasis | None],
) -> ReleaseFact:
    validate_identity(identity)
    validate_name(request_id, 'request id')
    if operation not in ('file', 'withdraw'):
        raise ValueError('unsupported release operation')
    expected = ReleaseFact(identity, request_id, operation)
    existing = find_release_fact(db, identity.scan_id)
    if existing is not None:
        return _confirm_matching_release(existing, expected)

    # Query the authority before taking the device write lock. Its terminal is immutable.
    basis = lookup_basis(identity.scan_id)
    if basis is None or ReleaseFact(basis.identity, basis.request_id, basis.operation) != expected:
        raise UnauthorizedRelease('no committed disposition for this release')
    if operation == 'file' and basis.result_id is None:
        raise UnauthorizedRelease('file release requires a recoverable result')
    if operation == 'withdraw' and basis.result_id is not None:
        raise UnauthorizedRelease('withdrawal cannot release a published original')

    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = find_release_fact(db, identity.scan_id)
        if existing is not None:
            return _confirm_matching_release(existing, expected)
        if find_capture_identity(db, identity.scan_id) != identity:
            raise UnauthorizedRelease('release identity does not match the device')
        deleted = db.execute(
            'DELETE FROM device_slot WHERE slot_no = 1 AND scan_id = ?',
            (identity.scan_id,),
        )
        if deleted.rowcount != 1:
            raise UnauthorizedRelease('release does not name the occupied slot')
        db.execute(
            'INSERT INTO device_releases (scan_id, request_id, operation) VALUES (?, ?, ?)',
            (identity.scan_id, request_id, operation),
        )
    return expected


class DevicePort:
    def __init__(
        self,
        db: sqlite3.Connection,
        lookup_basis: Callable[[str], ReleaseBasis | None],
    ) -> None:
        self._db = db
        self._lookup_basis = lookup_basis

    def read(self, scan_id: str) -> ScanReadout:
        return read_scan(self._db, scan_id)

    def identity(self, scan_id: str) -> ScanIdentity | None:
        return find_capture_identity(self._db, scan_id)

    def released(self, scan_id: str) -> ReleaseFact | None:
        return find_release_fact(self._db, scan_id)

    def release(self, identity: ScanIdentity, request_id: str, operation: str) -> ReleaseFact:
        return release_scan(self._db, identity, request_id, operation, self._lookup_basis)
