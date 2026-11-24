import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from capture.readout import ScanReadout, read_review, read_scan
from capture.slots import (
    ScanIdentity,
    find_capture_identity,
    find_current_version,
    find_sealed_version,
    is_revisable_scan,
    validate_identity,
)
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


@dataclass(frozen=True, slots=True)
class SealBasis:
    identity: ScanIdentity
    request_id: str
    version: int
    result_id: str | None


@dataclass(frozen=True, slots=True)
class SealFact:
    identity: ScanIdentity
    request_id: str
    version: int


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


def find_seal_fact(db: sqlite3.Connection, scan_id: str) -> SealFact | None:
    row = db.execute(
        '''SELECT s.request_id, s.version_no, i.device_id, i.trigger_code
           FROM device_seals AS s
           JOIN capture_identities AS i ON i.scan_id = s.scan_id
           WHERE s.scan_id = ?''',
        (scan_id,),
    ).fetchone()
    if row is None:
        return None
    request_id, version, device_id, trigger_code = row
    return SealFact(
        ScanIdentity(scan_id, device_id, decode_integer(trigger_code)),
        request_id,
        version,
    )


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
    if is_revisable_scan(db, identity.scan_id):
        raise UnauthorizedRelease('revisable originals must be released through an approval seal')

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


def seal_revision(
    db: sqlite3.Connection,
    request_id: str,
    scan_id: str,
    version: int,
    lookup_seal_basis: Callable[[str], SealBasis | None],
) -> SealFact:
    """裁定重扫件核准：先持久封定，档案正式提交后再以同一请求确认放行。"""
    validate_name(request_id, 'request id')
    validate_name(scan_id, 'scan id')
    if type(version) is not int or version <= 0:
        raise ValueError('version must be a positive integer')

    existing_seal = find_seal_fact(db, scan_id)
    existing_release = find_release_fact(db, scan_id)
    if existing_release is not None:
        if (
            existing_release.request_id != request_id
            or existing_seal is None
            or existing_seal.version != version
        ):
            raise UnauthorizedRelease('existing approval release belongs to another disposition')
        return SealFact(existing_release.identity, request_id, version)

    with db:
        db.execute('BEGIN IMMEDIATE')
        existing_seal = find_seal_fact(db, scan_id)
        existing_release = find_release_fact(db, scan_id)
        current = find_current_version(db, scan_id)
        if current is None:
            raise UnauthorizedRelease(f'{scan_id} is not a revisable original')
        identity = find_capture_identity(db, scan_id)
        if identity is None:
            raise UnauthorizedRelease(f'unknown device original {scan_id}')
        if version > current:
            raise UnauthorizedRelease(f'version {version} was never produced for {scan_id}')
        if existing_seal is not None:
            if (
                existing_seal.request_id != request_id
                or existing_seal.version != version
            ):
                raise UnauthorizedRelease('another request already sealed this revision')
        else:
            # The archive must have durably registered the approval parameters first.
            # A registered-but-undecided basis only binds request id and version; the
            # device supplies the actual identity itself.
            basis = lookup_seal_basis(scan_id)
            if (
                basis is None
                or basis.request_id != request_id
                or basis.version != version
            ):
                raise UnauthorizedRelease('no registered approval request for this seal')
            if basis.identity is not None and basis.identity != identity:
                raise UnauthorizedRelease('registered approval names another device identity')
            db.execute(
                'INSERT INTO device_seals (scan_id, request_id, version_no) VALUES (?, ?, ?)',
                (scan_id, request_id, version),
            )

        # Only a formal, same-request, same-version archive entry may confirm the release.
        basis = lookup_seal_basis(scan_id)
        if basis is not None and basis.result_id is not None:
            sealed_version = find_sealed_version(db, scan_id)
            if sealed_version != version:
                raise UnauthorizedRelease('sealed revision does not match the approval request')
            if (
                basis.request_id != request_id
                or basis.version != version
                or basis.identity != identity
            ):
                raise UnauthorizedRelease('approval release basis does not match the device seal')
            if existing_release is None:
                deleted = db.execute(
                    'DELETE FROM device_slot WHERE slot_no = 1 AND scan_id = ?',
                    (scan_id,),
                )
                if deleted.rowcount != 1:
                    raise UnauthorizedRelease('approval release does not name the occupied slot')
                db.execute(
                    'INSERT INTO device_releases (scan_id, request_id, operation) '
                    "VALUES (?, ?, 'approve')",
                    (scan_id, request_id),
                )
            existing_release = find_release_fact(db, scan_id)

    fact = find_seal_fact(db, scan_id)
    if fact is None or fact.request_id != request_id or fact.version != version:
        raise UnauthorizedRelease('seal could not be confirmed')
    return fact


class DevicePort:
    def __init__(
        self,
        db: sqlite3.Connection,
        lookup_basis: Callable[[str], ReleaseBasis | None],
        lookup_seal_basis: Callable[[str], SealBasis | None] | None = None,
    ) -> None:
        self._db = db
        self._lookup_basis = lookup_basis
        self._lookup_seal_basis = lookup_seal_basis

    def read(self, scan_id: str) -> ScanReadout:
        return read_scan(self._db, scan_id)

    def review(self, scan_id: str) -> tuple[int, ScanReadout]:
        return read_review(self._db, scan_id)

    def identity(self, scan_id: str) -> ScanIdentity | None:
        return find_capture_identity(self._db, scan_id)

    def is_revisable(self, scan_id: str) -> bool:
        return is_revisable_scan(self._db, scan_id)

    def current_version(self, scan_id: str) -> int | None:
        return find_current_version(self._db, scan_id)

    def sealed_version(self, scan_id: str) -> int | None:
        return find_sealed_version(self._db, scan_id)

    def released(self, scan_id: str) -> ReleaseFact | None:
        return find_release_fact(self._db, scan_id)

    def seal(self, scan_id: str) -> SealFact | None:
        return find_seal_fact(self._db, scan_id)

    def release(self, identity: ScanIdentity, request_id: str, operation: str) -> ReleaseFact:
        return release_scan(self._db, identity, request_id, operation, self._lookup_basis)

    def seal_revision(
        self, request_id: str, scan_id: str, version: int
    ) -> SealFact:
        if self._lookup_seal_basis is None:
            raise UnauthorizedRelease('no approval authority configured for revision seals')
        return seal_revision(
            self._db, request_id, scan_id, version, self._lookup_seal_basis
        )
