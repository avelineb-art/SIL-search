"""Australian state/territory and postcode extraction from page text.

Used to populate `states` and `service_locations` on a provider - not for
extracting individual residential addresses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_STATE_ABBREVIATIONS = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]

_STATE_FULL_NAMES = {
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "QLD": "Queensland",
    "WA": "Western Australia",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "ACT": "Australian Capital Territory",
    "NT": "Northern Territory",
}

# Standard Australia Post postcode ranges per state/territory.
_POSTCODE_RANGES: dict[str, list[tuple[int, int]]] = {
    "NSW": [(1000, 1999), (2000, 2599), (2619, 2899), (2921, 2999)],
    "ACT": [(200, 299), (2600, 2618), (2900, 2920)],
    "VIC": [(3000, 3999), (8000, 8999)],
    "QLD": [(4000, 4999), (9000, 9999)],
    "SA": [(5000, 5799), (5800, 5999)],
    "WA": [(6000, 6797), (6800, 6999)],
    "TAS": [(7000, 7799), (7800, 7999)],
    "NT": [(800, 899), (900, 999)],
}

_ABBR_PATTERN = re.compile(r"\b(" + "|".join(_STATE_ABBREVIATIONS) + r")\b")
_FULL_NAME_PATTERN = re.compile(
    "|".join(re.escape(name) for name in _STATE_FULL_NAMES.values()), re.IGNORECASE
)
_STATE_POSTCODE_PATTERN = re.compile(
    r"\b(" + "|".join(_STATE_ABBREVIATIONS) + r")\s+(\d{4})\b"
    r"|\b(\d{4})\s+(" + "|".join(_STATE_ABBREVIATIONS) + r")\b"
)


def postcode_to_state(postcode: str) -> str | None:
    try:
        value = int(postcode)
    except ValueError:
        return None
    for state, ranges in _POSTCODE_RANGES.items():
        if any(low <= value <= high for low, high in ranges):
            return state
    return None


def extract_states(text: str) -> list[str]:
    found: set[str] = set()
    for match in _ABBR_PATTERN.finditer(text):
        found.add(match.group(1))
    for match in _FULL_NAME_PATTERN.finditer(text):
        for abbr, full in _STATE_FULL_NAMES.items():
            if match.group(0).lower() == full.lower():
                found.add(abbr)
    return sorted(found)


@dataclass(frozen=True)
class LocationMention:
    state: str
    postcode: str | None
    matched_text: str


def extract_service_locations(text: str) -> list[LocationMention]:
    """Find state+postcode pairs (either order), the strongest signal of an
    advertised service location in free text (e.g. "Parramatta NSW 2150").
    """
    mentions: list[LocationMention] = []
    seen: set[tuple[str, str | None]] = set()
    for match in _STATE_POSTCODE_PATTERN.finditer(text):
        if match.group(1):
            state, postcode = match.group(1), match.group(2)
        else:
            postcode, state = match.group(3), match.group(4)
        key = (state, postcode)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(LocationMention(state=state, postcode=postcode, matched_text=match.group(0)))
    return mentions
