import sqlite3

from capture.release import DevicePort, PortUnavailable
from disposition.decision import DispositionHook, advance_disposition, notify
from disposition.journal import DispositionRequest, bind_request
from disposition.receipts import Receipt, build_receipt
from quality.plan import validate_name


def validate_request(request_id: str, scan_id: str, operation: str) -> DispositionRequest:
    validate_name(request_id, 'request id')
    validate_name(scan_id, 'scan id')
    if operation not in ('file', 'withdraw'):
        raise ValueError('operation must be file or withdraw')
    return DispositionRequest(request_id, scan_id, operation)


def submit(
    db: sqlite3.Connection,
    device: DevicePort,
    request_id: str,
    scan_id: str,
    operation: str,
    *,
    hook: DispositionHook | None = None,
) -> Receipt:
    request = bind_request(db, validate_request(request_id, scan_id, operation))
    try:
        notify(hook, 'request_registered', request)
    except PortUnavailable:
        return build_receipt(request)
    return advance_disposition(db, device, request, hook=hook)
