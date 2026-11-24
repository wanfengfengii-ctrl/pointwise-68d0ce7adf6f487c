from dataclasses import dataclass

from intake.schema import StagedScan


@dataclass(frozen=True, slots=True)
class TriggerProvenance:
    scan_id: str
    device_id: str
    trigger: int


def freeze_provenance(scan: StagedScan) -> TriggerProvenance:
    identity = scan.identity
    return TriggerProvenance(identity.scan_id, identity.device_id, identity.trigger)
