from collections.abc import Sequence


def validate_reading(value: int) -> int:
    if type(value) is not int:
        raise ValueError('a reading must be an integer')
    return value


def validate_readings(values: Sequence[int], *, points: int | None = None) -> tuple[int, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError('readings must be an ordered sequence of integers')
    readings = tuple(validate_reading(value) for value in values)
    if not readings:
        raise ValueError('a scan must contain at least one reading')
    if points is not None and len(readings) != points:
        raise ValueError(f'expected {points} readings, received {len(readings)}')
    return readings


def encode_integer(value: int) -> str:
    # Hex avoids SQLite integer limits and Python's decimal text digit limit.
    return format(validate_reading(value), 'x')


def decode_integer(value: str) -> int:
    number = int(value, 16)
    if encode_integer(number) != value:
        raise ValueError('stored integer is not in canonical form')
    return number
