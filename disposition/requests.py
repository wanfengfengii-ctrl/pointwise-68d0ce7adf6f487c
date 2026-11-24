import sqlite3

from capture.release import DevicePort, PortUnavailable
from disposition.approvals import ApprovalReceipt, approve as _approve
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


def _reject_revisable(device: DevicePort, scan_id: str) -> None:
    # 重扫件只能走核准入口，普通入档/撤销不得绕过核准，直接放行也受同一资格约束。
    if device.is_revisable(scan_id):
        raise ValueError(f'{scan_id} is a revisable original and requires approval')


def submit(
    db: sqlite3.Connection,
    device: DevicePort,
    request_id: str,
    scan_id: str,
    operation: str,
    *,
    hook: DispositionHook | None = None,
) -> Receipt:
    request = validate_request(request_id, scan_id, operation)
    _reject_revisable(device, scan_id)
    request = bind_request(db, request)
    try:
        notify(hook, 'request_registered', request)
    except PortUnavailable:
        return build_receipt(request)
    return advance_disposition(db, device, request, hook=hook)


def approve(
    db: sqlite3.Connection,
    device: DevicePort,
    request_id: str,
    scan_id: str,
    version: int,
) -> ApprovalReceipt:
    return _approve(db, device, request_id, scan_id, version)
