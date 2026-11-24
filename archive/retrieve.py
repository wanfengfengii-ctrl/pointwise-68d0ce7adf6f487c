import sqlite3

from archive.catalog import find_result
from archive.payload import FixedPayload, load_fixed_payload
from quality.plan import validate_name


class UnknownResult(LookupError):
    pass


def retrieve(db: sqlite3.Connection, result_id: str) -> FixedPayload:
    validate_name(result_id, 'result id')
    entry = find_result(db, result_id)
    if entry is None:
        raise UnknownResult(f'unknown formal result {result_id}')
    payload = load_fixed_payload(db, entry.result_id)
    if payload is None or payload.provenance.scan_id != entry.scan_id:
        raise ValueError(f'incomplete fixed payload for {result_id}')
    return payload
