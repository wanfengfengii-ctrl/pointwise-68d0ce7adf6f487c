import sqlite3
from dataclasses import dataclass

from archive.revision_publish import publish_revision
from capture.readout import UnknownScan
from capture.release import DevicePort, PortUnavailable, SealBasis
from disposition.revision_journal import (
    RevisionApproval,
    bind_revision_request,
    find_revision_decision,
    mark_revision_result,
    record_revision_decision,
    save_revision_progress,
)
from intake.integrity import validate_readout
from intake.review_staging import load_review_staged_scan
from quality.assessment import assess_scan
from quality.plan import validate_name


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    request_id: str
    scan_id: str
    version: int
    status: str
    result_id: str | None


def _pending(approval: RevisionApproval) -> ApprovalReceipt:
    return ApprovalReceipt(
        approval.request_id, approval.scan_id, approval.version, 'pending', None
    )


def lookup_seal_basis(db: sqlite3.Connection, scan_id: str) -> SealBasis | None:
    """设备端口裁定封定/放行时查询档案端已持久的核准依据（只读已提交数据）。

    已写终局的请求带同版身份与成果号；仅登记未终局的请求提供请求/版别绑定，
    设备身份由端口在设备库内自行核对。
    """
    if db.in_transaction:
        raise PortUnavailable('approval authority must read committed data')
    decision = find_revision_decision(db, scan_id)
    if decision is not None:
        return SealBasis(
            decision['identity'],
            decision['request'].request_id,
            decision['request'].version,
            decision['result_id'],
        )
    registered = db.execute(
        '''SELECT request_id, version_no FROM revision_requests
           WHERE scan_id = ? ORDER BY version_no DESC LIMIT 1''',
        (scan_id,),
    ).fetchone()
    if registered is None:
        return None
    return SealBasis(None, registered[0], registered[1], None)


def _load_complete_review(db, device, scan_id, version):
    """取得同一请求、同版的完整原读：优先已暂存版本，否则当场读取当前实扫。"""
    identity = device.identity(scan_id)
    staged = load_review_staged_scan(db, scan_id, version)
    if staged is None:
        actual_version, readout = device.review(scan_id)
        if actual_version != version:
            return None
        staged = validate_readout(scan_id, readout)
    if staged.identity != identity:
        return None
    return staged


def approve(
    db: sqlite3.Connection,
    device: DevicePort,
    request_id: str,
    scan_id: str,
    version: int,
) -> ApprovalReceipt:
    """可重扫件只按已查阅的实扫版核准入档；首次办理与重投同一入口。"""
    validate_name(request_id, 'request id')
    validate_name(scan_id, 'scan id')
    if type(version) is not int or version <= 0:
        raise ValueError('version must be a positive integer')
    if not device.is_revisable(scan_id):
        raise ValueError(f'{scan_id} is not a revisable original')

    # 同号绑定原件及版别；异参由 bind 内的 RequestParameterConflict 拒绝。
    approval = bind_revision_request(
        db, RevisionApproval(request_id, scan_id, version)
    )
    try:
        current = device.current_version(scan_id)
        if current is None:
            raise UnknownScan(f'no revisable device original for {scan_id}')
        if version != current:
            # 所阅版本已被重扫替换：旧核准过期，不签发成果也不放行占槽。
            return ApprovalReceipt(request_id, scan_id, version, 'obsolete', None)

        identity = device.identity(scan_id)
        decision = find_revision_decision(db, scan_id)
        if decision is not None and decision['request'].request_id != request_id:
            # 别号已封定的同版不可再次核准。
            raise ValueError(
                f'{scan_id} version {version} already sealed by '
                f'{decision["request"].request_id}'
            )
        if decision is not None and decision['result_id'] is not None:
            # 同参续办或复领：成果已签存，确认设备放行后直接复领，不依赖当前槽。
            if decision['request'].version != version:
                raise ValueError(
                    f'request {request_id} is bound to version '
                    f'{decision["request"].version}'
                )
            device.seal_revision(request_id, scan_id, version)
            save_revision_progress(db, request_id, decision['result_id'])
            return ApprovalReceipt(
                request_id, scan_id, version, 'filed', decision['result_id']
            )

        staged = _load_complete_review(db, device, scan_id, version)
        if staged is None:
            # 取不到同一版的完整原读：不签发，保留可续办的原请求。
            return _pending(approval)
        assessment = assess_scan(staged.plan, staged.values)

        sealed = device.seal(scan_id)
        if sealed is not None and (
            sealed.request_id != request_id or sealed.version != version
        ):
            raise ValueError(
                f'{scan_id} is already sealed by request {sealed.request_id}'
            )

        # 首次向端口发请求前，核准参数已由 bind_revision_request 持久登记。
        if sealed is None:
            device.seal_revision(request_id, scan_id, version)
            sealed = device.seal(scan_id)
            if sealed is None or sealed.request_id != request_id:
                return _pending(approval)

        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                decision = find_revision_decision(db, scan_id)
                if decision is None:
                    record_revision_decision(db, approval, identity, result_id=None)
                result_id = publish_revision(
                    db, staged, assessment, request_id, version
                )
                mark_revision_result(db, scan_id, result_id)
        except sqlite3.DatabaseError:
            # 档案提交故障（含提交约束失败）：事务回滚，请求仍可按原参数续办，
            # 设备继续占槽，不出成果也不放行。
            return _pending(approval)

        # 正式提交成功之后，才按同一核准、同一版别确认设备放行。
        device.seal_revision(request_id, scan_id, version)
        save_revision_progress(db, request_id, result_id)
        return ApprovalReceipt(request_id, scan_id, version, 'filed', result_id)
    except PortUnavailable:
        # 端口失答：返回待办，重开后按原请求续办。
        return _pending(approval)
