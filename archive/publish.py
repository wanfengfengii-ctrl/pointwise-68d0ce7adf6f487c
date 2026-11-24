import sqlite3

from archive.catalog import find_result_for_scan, register_result
from archive.payload import FixedPayload, store_fixed_payload
from archive.provenance import freeze_provenance
from disposition.journal import find_terminal
from intake.schema import StagedScan
from quality.assessment import QualityAssessment


class PublicationDenied(ValueError):
    pass


def publish_archive(
    db: sqlite3.Connection, scan: StagedScan, assessment: QualityAssessment, request_id: str,
) -> str:
    if not db.in_transaction:
        raise PublicationDenied('publishing requires a disposition transaction')
    terminal = find_terminal(db, scan.identity.scan_id)
    if (
        terminal is None
        or terminal.request.request_id != request_id
        or terminal.request.operation != 'file'
        or terminal.identity != scan.identity
    ):
        raise PublicationDenied('publication requires the matching file terminal')
    if (assessment.plan_id, assessment.points, assessment.unit) != (
        scan.plan.plan_id, scan.plan.points, scan.plan.unit,
    ):
        raise PublicationDenied('quality assessment does not match the staged plan')

    existing = find_result_for_scan(db, scan.identity.scan_id)
    if existing is not None:
        return existing.result_id
    entry = register_result(db, scan.identity.scan_id)
    payload = FixedPayload(
        entry.result_id, freeze_provenance(scan), assessment.unit, scan.values, assessment.spread,
    )
    store_fixed_payload(db, payload)
    return entry.result_id
