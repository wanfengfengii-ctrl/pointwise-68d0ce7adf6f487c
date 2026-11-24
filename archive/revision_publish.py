import sqlite3

from archive.catalog import find_result_for_scan, register_result
from archive.payload import FixedPayload, store_fixed_payload
from archive.provenance import TriggerProvenance
from disposition.revision_journal import find_revision_decision
from intake.schema import StagedScan
from quality.assessment import QualityAssessment


class RevisionPublicationDenied(ValueError):
    pass


def publish_revision(
    db: sqlite3.Connection,
    scan: StagedScan,
    assessment: QualityAssessment,
    request_id: str,
    version: int,
) -> str:
    """在核准事务内固定重扫实扫版的正式成果。同一请求、同版重投为幂等。"""
    if not db.in_transaction:
        raise RevisionPublicationDenied('publishing requires an approval transaction')
    decision = find_revision_decision(db, scan.identity.scan_id)
    if (
        decision is None
        or decision['request'].request_id != request_id
        or decision['request'].version != version
        or decision['identity'] != scan.identity
    ):
        raise RevisionPublicationDenied('publication requires the matching approval decision')
    if (assessment.plan_id, assessment.points, assessment.unit) != (
        scan.plan.plan_id, scan.plan.points, scan.plan.unit,
    ):
        raise RevisionPublicationDenied('quality assessment does not match the staged plan')

    existing = find_result_for_scan(db, scan.identity.scan_id)
    if existing is not None:
        return existing.result_id
    entry = register_result(db, scan.identity.scan_id)
    payload = FixedPayload(
        entry.result_id,
        TriggerProvenance(
            scan.identity.scan_id, scan.identity.device_id, scan.identity.trigger
        ),
        assessment.unit,
        scan.values,
        assessment.spread,
        version,
    )
    store_fixed_payload(db, payload)
    return entry.result_id
