from capture.readout import ReadoutHeader, ReadoutRow, ScanReadout
from capture.slots import validate_identity
from intake.schema import StagedReading, StagedScan
from quality.plan import validate_name, validate_plan
from quality.readings import validate_reading


def validate_readout(scan_id: str, readout: ScanReadout) -> StagedScan:
    validate_name(scan_id, 'scan id')
    if not isinstance(readout, ScanReadout) or not isinstance(readout.header, ReadoutHeader):
        raise ValueError('device must provide a scan readout with a header')
    header = readout.header
    validate_identity(header.identity)
    validate_plan(header.plan)
    if header.identity.scan_id != scan_id:
        raise ValueError('header names a different original')
    if header.identity.device_id != header.plan.source or header.unit != header.plan.unit:
        raise ValueError('header source or unit does not match its plan')
    if not isinstance(readout.rows, (tuple, list)) or len(readout.rows) != header.plan.points:
        raise ValueError('readout does not contain the planned number of readings')

    readings = []
    for position, row in enumerate(readout.rows, 1):
        if not isinstance(row, ReadoutRow) or type(row.position) is not int or row.position != position:
            raise ValueError(f'invalid readout position {position}')
        validate_identity(row.identity)
        if row.identity != header.identity or row.unit != header.unit:
            raise ValueError(f'provenance or unit mismatch at position {position}')
        readings.append(StagedReading(position, validate_reading(row.value)))
    return StagedScan(header.identity, header.plan, tuple(readings))
