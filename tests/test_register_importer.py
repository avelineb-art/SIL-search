from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from sil_research.database import ProviderRegisterSnapshot, RegisterEntry
from sil_research.register.base import ColumnMapping, RegisterImportError
from sil_research.register.importer import build_entry_data, import_register_file, resolve_columns

ALIASES = {
    "abn": ["ABN"],
    "entity_name": ["Legal Name"],
    "trading_name": ["Trading Name"],
    "registration_status": ["Registration Status"],
    "registration_groups": ["Registration Groups"],
    "state": ["State"],
}


def test_resolve_columns_matches_known_aliases_case_insensitively():
    headers = ["abn ", "Legal Name", "Trading Name", "State"]
    mapping = resolve_columns(headers, ALIASES)
    assert mapping.logical_to_actual["abn"] == "abn "
    assert mapping.logical_to_actual["entity_name"] == "Legal Name"
    assert mapping.logical_to_actual["trading_name"] == "Trading Name"


def test_resolve_columns_raises_loudly_when_required_column_missing():
    # Simulates the register export's structure changing - "ABN" renamed to
    # something not in our alias list.
    headers = ["Provider Identifier", "Legal Name"]
    with pytest.raises(RegisterImportError) as excinfo:
        resolve_columns(headers, ALIASES)
    message = str(excinfo.value)
    assert "abn" in message.lower()
    assert "Provider Identifier" in message


def test_build_entry_data_parses_and_normalises_row():
    mapping = ColumnMapping(
        logical_to_actual={"abn": "ABN", "entity_name": "Legal Name", "trading_name": "Trading Name", "registration_groups": "Registration Groups"},
        all_headers=["ABN", "Legal Name", "Trading Name", "Registration Groups"],
    )
    raw_row = {
        "ABN": "51 824 753 556",
        "Legal Name": "Sunrise Living Pty Ltd",
        "Trading Name": "Sunrise Living",
        "Registration Groups": "Assist Personal Activities; Development Life Skills",
    }
    fields = build_entry_data(raw_row, mapping)
    assert fields["abn"] == "51824753556"
    assert fields["entity_name"] == "Sunrise Living Pty Ltd"
    assert fields["normalised_entity_name"] == "sunrise living pty ltd"
    assert fields["registration_groups"] == ["Assist Personal Activities", "Development Life Skills"]


def test_build_entry_data_skips_row_with_no_abn_or_name():
    mapping = ColumnMapping(logical_to_actual={"abn": "ABN", "entity_name": "Legal Name"}, all_headers=["ABN", "Legal Name"])
    assert build_entry_data({"ABN": "", "Legal Name": ""}, mapping) is None


def test_import_register_file_creates_snapshot_and_entries(tmp_path, db_session):
    csv_path = tmp_path / "register.csv"
    csv_path.write_text(
        "ABN,Legal Name,Trading Name,Registration Status,State\n"
        "51 824 753 556,Sunrise Living Pty Ltd,Sunrise Living,Active,NSW\n"
        "20 100 000 004,Beacon Support Services Pty Ltd,Beacon Support,Active,SA\n",
        encoding="utf-8",
    )

    result = import_register_file(csv_path, date(2026, 7, 1), db_session, column_aliases=ALIASES)
    db_session.commit()

    assert result.imported == 2
    assert result.skipped == 0
    assert result.row_count == 2

    snapshot = db_session.get(ProviderRegisterSnapshot, result.snapshot_id)
    assert snapshot.row_count == 2

    entries = list(db_session.execute(select(RegisterEntry).where(RegisterEntry.snapshot_id == result.snapshot_id)).scalars())
    assert len(entries) == 2
    abns = {e.abn for e in entries}
    assert abns == {"51824753556", "20100000004"}


def test_import_register_file_snapshots_are_additive_and_versioned(tmp_path, db_session):
    csv_path = tmp_path / "register.csv"
    csv_path.write_text("ABN,Legal Name\n51 824 753 556,Sunrise Living Pty Ltd\n", encoding="utf-8")

    first = import_register_file(csv_path, date(2026, 1, 1), db_session, column_aliases=ALIASES)
    db_session.commit()
    second = import_register_file(csv_path, date(2026, 7, 1), db_session, column_aliases=ALIASES)
    db_session.commit()

    assert first.snapshot_id != second.snapshot_id
    snapshots = list(db_session.execute(select(ProviderRegisterSnapshot)).scalars())
    assert len(snapshots) == 2

    first_entries = list(db_session.execute(select(RegisterEntry).where(RegisterEntry.snapshot_id == first.snapshot_id)).scalars())
    second_entries = list(db_session.execute(select(RegisterEntry).where(RegisterEntry.snapshot_id == second.snapshot_id)).scalars())
    assert len(first_entries) == 1
    assert len(second_entries) == 1


def test_import_register_file_raises_on_structure_change_without_partial_commit(tmp_path, db_session):
    csv_path = tmp_path / "register.csv"
    # "ABN" column renamed/removed entirely - simulates the register export
    # changing shape between quarterly downloads.
    csv_path.write_text("Provider Identifier,Legal Name\nXX-1234,Sunrise Living Pty Ltd\n", encoding="utf-8")

    with pytest.raises(RegisterImportError):
        import_register_file(csv_path, date(2026, 7, 1), db_session, column_aliases=ALIASES)

    assert db_session.execute(select(ProviderRegisterSnapshot)).scalars().first() is None
    assert db_session.execute(select(RegisterEntry)).scalars().first() is None


def test_import_register_file_missing_file_raises():
    from pathlib import Path

    with pytest.raises(RegisterImportError):
        import_register_file(Path("/nonexistent/register.csv"), date(2026, 7, 1), session=None, column_aliases=ALIASES)


def test_import_register_file_supports_xlsx(tmp_path, db_session):
    from openpyxl import Workbook

    xlsx_path = tmp_path / "register.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ABN", "Legal Name"])
    sheet.append(["51 824 753 556", "Sunrise Living Pty Ltd"])
    workbook.save(xlsx_path)

    result = import_register_file(xlsx_path, date(2026, 7, 1), db_session, column_aliases=ALIASES)
    db_session.commit()

    assert result.imported == 1
    entries = list(db_session.execute(select(RegisterEntry).where(RegisterEntry.snapshot_id == result.snapshot_id)).scalars())
    assert entries[0].abn == "51824753556"
