import sqlite3
from collections.abc import Callable

from capture.readout import ScanReadout
from intake.integrity import validate_readout
from intake.review_staging import store_review_staged_scan
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


def stage_review(
    db: sqlite3.Connection,
    scan_id: str,
    version: int,
    reader: Callable[[str], tuple[int, ScanReadout]],
) -> StagedScan:
    """预先暂存可重扫件指定版别的完整原读。

    reader 按 reader(scan_id) 提供 (实际版别, ScanReadout)。实际版别与请求版别
    不同，或件头与原行不一致时报 ValueError，整份不入账。
    """
    validate_name(scan_id, 'scan id')
    if type(version) is not int or version <= 0:
        raise ValueError('version must be a positive integer')
    actual_version, readout = reader(scan_id)
    if type(actual_version) is not int or actual_version <= 0:
        raise ValueError('reader must return the current positive revision version')
    if actual_version != version:
        raise ValueError(
            f'requested version {version} is no longer current (actual {actual_version})'
        )
    scan = validate_readout(scan_id, readout)
    return store_review_staged_scan(db, version, scan)
