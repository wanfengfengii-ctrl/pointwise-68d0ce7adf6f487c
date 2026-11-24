import sqlite3

from capture.release import DevicePort
from disposition.decision import DispositionHook, advance_disposition
from disposition.journal import load_request, require_matching_request
from disposition.receipts import Receipt
from disposition.requests import validate_request


def retry(
    db: sqlite3.Connection,
    device: DevicePort,
    request_id: str,
    scan_id: str,
    operation: str,
    *,
    hook: DispositionHook | None = None,
) -> Receipt:
    requested = validate_request(request_id, scan_id, operation)
    if device.is_revisable(scan_id):
        raise ValueError(f'{scan_id} is a revisable original and requires approval')
    request = require_matching_request(load_request(db, request_id), requested)
    return advance_disposition(db, device, request, hook=hook)
