import json
import sqlite3
from dataclasses import dataclass

from archive.provenance import TriggerProvenance
from quality.readings import decode_integer, encode_integer


@dataclass(frozen=True, slots=True)
class FixedPayload:
    result_id: str
    provenance: TriggerProvenance
    unit: str
    readings: tuple[int, ...]
    spread: int
    version: int | None = None


_PAYLOAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_payloads (
    result_id TEXT PRIMARY KEY NOT NULL REFERENCES archive_catalog (result_id),
    scan_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    trigger_code TEXT NOT NULL,
    unit TEXT NOT NULL,
    readings_json TEXT NOT NULL,
    spread_code TEXT NOT NULL
);
"""


def initialize_payload_store(db: sqlite3.Connection) -> None:
    db.executescript(_PAYLOAD_SCHEMA)
    columns = {
        row[1] for row in db.execute('PRAGMA table_info(archive_payloads)').fetchall()
    }
    if 'version_no' not in columns:
        # 普通班成果没有版别；重扫核准成果在该列记录获准实扫版。
        db.execute('ALTER TABLE archive_payloads ADD COLUMN version_no INTEGER')


def store_fixed_payload(db: sqlite3.Connection, payload: FixedPayload) -> None:
    if not db.in_transaction:
        raise ValueError('fixed data must be written in the publication transaction')
    origin = payload.provenance
    db.execute(
        '''INSERT INTO archive_payloads
           (result_id, scan_id, device_id, trigger_code, unit, readings_json,
            spread_code, version_no)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            payload.result_id, origin.scan_id, origin.device_id, encode_integer(origin.trigger),
            payload.unit, json.dumps([encode_integer(value) for value in payload.readings]),
            encode_integer(payload.spread), payload.version,
        ),
    )


def load_fixed_payload(db: sqlite3.Connection, result_id: str) -> FixedPayload | None:
    row = db.execute(
        '''SELECT scan_id, device_id, trigger_code, unit, readings_json, spread_code, version_no
           FROM archive_payloads WHERE result_id = ?''',
        (result_id,),
    ).fetchone()
    if row is None:
        return None
    scan_id, device_id, trigger_code, unit, readings_json, spread_code, version = row
    return FixedPayload(
        result_id,
        TriggerProvenance(scan_id, device_id, decode_integer(trigger_code)),
        unit,
        tuple(decode_integer(value) for value in json.loads(readings_json)),
        decode_integer(spread_code),
        version,
    )
