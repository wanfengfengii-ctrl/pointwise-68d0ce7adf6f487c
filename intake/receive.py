import sqlite3
from collections.abc import Callable

from capture.readout import ScanReadout
from intake.integrity import validate_readout
from intake.schema import StagedScan
from intake.staging import store_staged_scan
from quality.plan import validate_name


def stage(
    db: sqlite3.Connection,
    scan_id: str,
    reader: Callable[[str], ScanReadout],
) -> StagedScan:
    validate_name(scan_id, 'scan id')
    readout = reader(scan_id)
    return store_staged_scan(db, validate_readout(scan_id, readout))
