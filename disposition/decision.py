import sqlite3
from collections.abc import Callable

from archive.catalog import find_result_for_scan
from archive.payload import load_fixed_payload
from archive.provenance import TriggerProvenance
from archive.publish import publish_archive
from capture.readout import UnknownScan
from capture.release import DevicePort, PortUnavailable, ReleaseBasis, ReleaseFact, UnauthorizedRelease
from disposition.journal import (
    DispositionProgress,
    DispositionRequest,
    TerminalDecision,
    find_terminal,
    load_progress,
    record_terminal,
    save_progress,
)
from disposition.receipts import Receipt, build_receipt
from intake.staging import load_staged_scan
from quality.assessment import assess_scan


DispositionHook = Callable[[str, DispositionRequest], None]


class DispositionConflict(RuntimeError):
    def __init__(self, request: DispositionRequest, winner: DispositionRequest) -> None:
        self.request = request
        self.winner = winner
        super().__init__(f'{request.scan_id} already decided by {winner.request_id} ({winner.operation})')


def notify(hook: DispositionHook | None, event: str, request: DispositionRequest) -> None:
    if hook is not None:
        hook(event, request)


def lookup_release_basis(db: sqlite3.Connection, scan_id: str) -> ReleaseBasis | None:
    if db.in_transaction:
        raise PortUnavailable('disposition authority must read committed data')
    terminal = find_terminal(db, scan_id)
    if terminal is None:
        return None
    request = terminal.request
    identity = terminal.identity
    entry = find_result_for_scan(db, scan_id)
    if request.operation == 'withdraw':
        if entry is None:
            return ReleaseBasis(identity, request.request_id, request.operation, None)
        return None
    if entry is None:
        return None
    payload = load_fixed_payload(db, entry.result_id)
    provenance = TriggerProvenance(identity.scan_id, identity.device_id, identity.trigger)
    if payload is None or payload.provenance != provenance:
        return None
    return ReleaseBasis(identity, request.request_id, request.operation, entry.result_id)


def _check_winner(terminal: TerminalDecision, request: DispositionRequest) -> TerminalDecision:
    if terminal.request != request:
        raise DispositionConflict(request, terminal.request)
    return terminal


def _decide(
    db: sqlite3.Connection,
    device: DevicePort,
    request: DispositionRequest,
    hook: DispositionHook | None,
) -> TerminalDecision | None:
    existing = find_terminal(db, request.scan_id)
    if existing is not None:
        return _check_winner(existing, request)

    identity = device.identity(request.scan_id)
    staged = None
    assessment = None
    if request.operation == 'file':
        staged = load_staged_scan(db, request.scan_id)
        if staged is not None:
            assessment = assess_scan(staged.plan, staged.values)
    notify(hook, 'before_decision', request)

    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = find_terminal(db, request.scan_id)
        if existing is not None:
            return _check_winner(existing, request)
        if identity is None:
            raise UnknownScan(f'unknown device original {request.scan_id}')
        if request.operation == 'file' and staged is None:
            return None
        if staged is not None and staged.identity != identity:
            raise ValueError('staging provenance does not match the device original')
        terminal = TerminalDecision(request, identity)
        record_terminal(db, terminal)
        if staged is not None and assessment is not None:
            publish_archive(db, staged, assessment, request.request_id)

    notify(hook, 'after_decision', request)
    return terminal


def _finish(
    db: sqlite3.Connection,
    device: DevicePort,
    terminal: TerminalDecision,
    hook: DispositionHook | None,
) -> DispositionProgress:
    request = terminal.request
    entry = find_result_for_scan(db, request.scan_id)
    if (request.operation == 'file') != (entry is not None):
        raise ValueError('formal catalogue conflicts with the committed disposition')
    result_id = None if entry is None else entry.result_id
    expected = ReleaseFact(terminal.identity, request.request_id, request.operation)
    fact = device.released(request.scan_id)
    if fact is None:
        notify(hook, 'before_release', request)
        fact = device.release(terminal.identity, request.request_id, request.operation)
        notify(hook, 'after_release', request)
    if fact != expected:
        raise UnauthorizedRelease('device release fact does not match the committed disposition')
    progress = DispositionProgress(result_id)
    return save_progress(db, request.request_id, progress)


def advance_disposition(
    db: sqlite3.Connection,
    device: DevicePort,
    request: DispositionRequest,
    *,
    hook: DispositionHook | None = None,
) -> Receipt:
    try:
        progress = load_progress(db, request.request_id)
        if progress is not None:
            return build_receipt(request, progress)
        terminal = _decide(db, device, request, hook)
        if terminal is None:
            return build_receipt(request, pending_reason='awaiting_stage')
        return build_receipt(request, _finish(db, device, terminal, hook))
    except (PortUnavailable, sqlite3.OperationalError):
        return build_receipt(request)
