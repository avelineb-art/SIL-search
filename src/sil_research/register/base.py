"""Shared types for register import and matching.

Kept free of any database/session dependency so both modules stay easy to
unit test against plain data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


class RegisterImportError(Exception):
    """Raised when the register file's structure can't be confidently
    mapped to the expected fields - deliberately fails loudly rather than
    guessing at a column by position, per the build spec.
    """


@dataclass(frozen=True)
class ColumnMapping:
    logical_to_actual: dict[str, str]
    all_headers: list[str]


@dataclass
class ImportResult:
    snapshot_id: int
    source_file: str
    download_date: date
    row_count: int
    imported: int
    skipped: int
    column_mapping: dict[str, str]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RegisterEntryData:
    """Read-model for one imported register row - matcher.py operates on
    these, not on the SQLAlchemy ORM row, so it can be tested without a
    database.
    """

    entry_id: int
    abn: str | None
    entity_name: str | None
    trading_name: str | None
    registration_status: str | None
    registration_groups: list[str]
    registration_expiry: datetime | None
    state: str | None
