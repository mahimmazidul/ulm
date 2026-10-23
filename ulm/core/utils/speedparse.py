"""Speed-string parser: '5M', '150m', '2G' → bytes/sec; '0'/off → 0."""
from __future__ import annotations

from typing import Union

UNITS = {
    "b": 1,
    "k": 1000, "kb": 1000, "kib": 1024,
    "m": 1000 * 1000, "mb": 1000 * 1000, "mib": 1024 * 1024,
    "g": 1000 * 1000 * 1000, "gb": 1000 * 1000 * 1000, "gib": 1024 ** 3,
}


def parse_speed(value: Union[str, int, float, None]) -> float:
    if value in (None, "", "0", "off"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(" ", "")
    number = ""
    unit = ""
    for character in text:
        if character.isdigit() or character == ".":
            number += character
        else:
            unit += character
    if not number:
        return 0.0
    multiplier = UNITS.get(unit, 1)
    return float(number) * multiplier
