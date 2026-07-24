from __future__ import annotations

from datetime import datetime

from sil_research.export.csv_export import evidence_to_rows, export_evidence_csv, export_providers_csv, provider_to_row
from sil_research.export.json_export import export_providers_json
from sil_research.models import EvidenceItem, ProviderRecord


def _make_provider() -> ProviderRecord:
    evidence = [
        EvidenceItem(
            evidence_type="exact_sil_phrase",
            source_url="https://example.com.au/services",
            page_title="Services",
            matched_text="supported independent living",
            context_excerpt="...offers supported independent living to...",
            score_contribution=5,
            extracted_at=datetime(2026, 1, 1),
        ),
        EvidenceItem(
            evidence_type="sil_vacancy",
            source_url="https://example.com.au/vacancies",
            page_title="Vacancies",
            matched_text="SIL vacancy",
            context_excerpt="...we have a SIL vacancy available...",
            score_contribution=5,
            extracted_at=datetime(2026, 1, 1),
        ),
    ]
    return ProviderRecord(
        provider_id="example.com.au",
        trading_name="Example Provider",
        domain="example.com.au",
        website_url="https://example.com.au/",
        sil_score=10,
        sil_evidence=evidence,
        states=["NSW", "VIC"],
    )


def test_provider_to_row_joins_evidence_urls_with_delimiter():
    row = provider_to_row(_make_provider())
    assert "https://example.com.au/services" in row["sil_evidence_urls"]
    assert "https://example.com.au/vacancies" in row["sil_evidence_urls"]
    assert " | " in row["sil_evidence_urls"]


def test_provider_to_row_preserves_states_list_with_delimiter():
    row = provider_to_row(_make_provider())
    assert row["states"] == "NSW | VIC"


def test_evidence_to_rows_has_one_row_per_evidence_item_with_source_url():
    rows = evidence_to_rows(_make_provider())
    assert len(rows) == 2
    urls = {r["source_url"] for r in rows}
    assert urls == {"https://example.com.au/services", "https://example.com.au/vacancies"}
    assert all(r["context_excerpt"] for r in rows)


def test_export_providers_csv_writes_header_and_row(tmp_path):
    output = tmp_path / "providers.csv"
    count = export_providers_csv([_make_provider()], output)
    assert count == 1
    content = output.read_text()
    header = content.splitlines()[0]
    assert "provider_id" in header
    assert "example.com.au" in content


def test_export_evidence_csv_one_row_per_item(tmp_path):
    output = tmp_path / "evidence.csv"
    count = export_evidence_csv([_make_provider()], output)
    assert count == 2


def test_export_providers_json_round_trips_evidence(tmp_path):
    import json

    output = tmp_path / "providers.json"
    export_providers_json([_make_provider()], output)
    payload = json.loads(output.read_text())
    assert len(payload) == 1
    assert len(payload[0]["sil_evidence"]) == 2
