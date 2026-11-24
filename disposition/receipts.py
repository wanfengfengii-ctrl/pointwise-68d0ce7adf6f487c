from dataclasses import dataclass

from disposition.journal import DispositionProgress, DispositionRequest


@dataclass(frozen=True, slots=True)
class FileReceipt:
    request_id: str
    scan_id: str
    result_id: str


@dataclass(frozen=True, slots=True)
class WithdrawalReceipt:
    request_id: str
    scan_id: str


@dataclass(frozen=True, slots=True)
class PendingReceipt:
    request_id: str
    scan_id: str
    reason: str


Receipt = FileReceipt | WithdrawalReceipt | PendingReceipt


def build_receipt(
    request: DispositionRequest,
    progress: DispositionProgress | None = None,
    *,
    pending_reason: str = 'unconfirmed',
) -> Receipt:
    if progress is None:
        return PendingReceipt(request.request_id, request.scan_id, pending_reason)
    if request.operation == 'file' and progress.result_id is not None:
        return FileReceipt(request.request_id, request.scan_id, progress.result_id)
    if request.operation == 'withdraw' and progress.result_id is None:
        return WithdrawalReceipt(request.request_id, request.scan_id)
    raise ValueError('confirmed progress does not match the request operation')
