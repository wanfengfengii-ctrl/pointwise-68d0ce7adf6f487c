from capture.slots import ScanIdentity
from intake.schema import StagedReading, StagedScan
from quality.plan import SamplingPlan, validate_plan
from quality.readings import decode_integer, encode_integer


HeaderRecord = tuple[str, str, str, str, int, str, str]
ReadingRecord = tuple[int, str]


def encode_staged_scan(scan: StagedScan) -> tuple[HeaderRecord, tuple[ReadingRecord, ...]]:
    identity = scan.identity
    plan = scan.plan
    header = (
        identity.scan_id, identity.device_id, encode_integer(identity.trigger),
        plan.plan_id, plan.points, plan.unit, plan.source,
    )
    rows = tuple((row.position, encode_integer(row.value)) for row in scan.readings)
    return header, rows


def decode_staged_scan(header: HeaderRecord, rows: tuple[ReadingRecord, ...]) -> StagedScan:
    scan_id, device_id, trigger_code, plan_id, points, unit, source = header
    plan = validate_plan(SamplingPlan(plan_id, points, unit, source))
    if len(rows) != points or any(row[0] != position for position, row in enumerate(rows, 1)):
        raise ValueError('stored staging is missing planned positions')
    if device_id != source:
        raise ValueError('stored staging does not match its plan source')
    return StagedScan(
        ScanIdentity(scan_id, device_id, decode_integer(trigger_code)),
        plan,
        tuple(StagedReading(position, decode_integer(value)) for position, value in rows),
    )
