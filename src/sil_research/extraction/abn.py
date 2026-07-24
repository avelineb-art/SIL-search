"""ABN (and ACN) extraction and checksum validation.

ABN Lookup API cross-checking (confirming an ABN is active and resolving
the legal entity name from it) is a Stage 3 concern - this module only
extracts candidate numbers from text and validates the checksum, which is
possible entirely offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]

_ABN_CONTEXT_PATTERN = re.compile(
    r"(?:australian business number|abn)\s*[:#]?\s*((?:\d[\s]?){11})",
    re.IGNORECASE,
)
_ACN_CONTEXT_PATTERN = re.compile(
    r"(?:australian company number|acn)\s*[:#]?\s*((?:\d[\s]?){9})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AbnMatch:
    raw_text: str
    digits: str
    valid_checksum: bool


def is_valid_abn_checksum(digits: str) -> bool:
    """Standard ABN checksum: subtract 1 from the first digit, multiply each
    digit by its weight, sum, and check divisibility by 89.
    """
    if not re.fullmatch(r"\d{11}", digits):
        return False
    values = [int(d) for d in digits]
    values[0] -= 1
    total = sum(v * w for v, w in zip(values, _ABN_WEIGHTS))
    return total % 89 == 0


def format_abn(digits: str) -> str:
    return f"{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:11]}"


def find_abns(text: str) -> list[AbnMatch]:
    """Find ABN-shaped numbers near the words "ABN" / "Australian Business
    Number" (formats: "ABN 12 345 678 901", "ABN: 12345678901",
    "Australian Business Number 12 345 678 901").
    """
    matches: list[AbnMatch] = []
    seen: set[str] = set()
    for match in _ABN_CONTEXT_PATTERN.finditer(text):
        digits = re.sub(r"\s", "", match.group(1))
        if len(digits) != 11 or digits in seen:
            continue
        seen.add(digits)
        matches.append(AbnMatch(raw_text=match.group(0), digits=digits, valid_checksum=is_valid_abn_checksum(digits)))
    return matches


def find_acns(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _ACN_CONTEXT_PATTERN.finditer(text):
        digits = re.sub(r"\s", "", match.group(1))
        if len(digits) == 9 and digits not in seen:
            seen.add(digits)
            found.append(digits)
    return found
