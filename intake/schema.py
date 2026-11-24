from dataclasses import dataclass

from capture.slots import ScanIdentity
from quality.plan import SamplingPlan


@dataclass(frozen=True, slots=True)
class StagedReading:
    position: int
    value: int


@dataclass(frozen=True, slots=True)
class StagedScan:
    identity: ScanIdentity
    plan: SamplingPlan
    readings: tuple[StagedReading, ...]

    @property
    def values(self) -> tuple[int, ...]:
        return tuple(reading.value for reading in self.readings)
