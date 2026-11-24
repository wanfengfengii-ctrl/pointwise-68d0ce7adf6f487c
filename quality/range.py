from collections.abc import Sequence

from quality.readings import validate_readings


def calculate_range(values: Sequence[int]) -> int:
    readings = validate_readings(values)
    return max(readings) - min(readings)
