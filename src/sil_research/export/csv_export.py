"""CSV export: one main provider-level file, plus a separate evidence file
with one row per evidence item (SIL and registration evidence are tagged
by an `evidence_kind` column so the two can be filtered from a single
file). Multiple URLs/excerpts are never concatenated into a single field
without a clear delimiter (` | `, configurable via settings.yml).
"""

from __future__ import annotations

import csv
from pathlib import Path

from sil_research.config import get_app_settings
from sil_research.models import EvidenceItem, ProviderRecord

PROVIDER_CSV_COLUMNS = [
    "provider_id",
    "provider_name",
    "legal_name",
    "abn",
    "abn_valid",
    "abn_lookup_confirmed",
    "domain",
    "website_url",
    "phone",
    "email",
    "address",
    "states",
    "service_locations",
    "sil_score",
    "sil_classification",
    "sil_confidence",
    "sil_evidence_summary",
    "sil_evidence_urls",
    "registration_claim_status",
    "registration_claim_confidence",
    "registration_claim_text",
    "registration_claim_urls",
    "register_match_status",
    "register_registration_status",
    "register_entity_name",
    "register_abn",
    "register_match_confidence",
    "register_snapshot_date",
    "automated_segment",
    "manual_review_status",
    "last_website_check",
    "last_register_check",
    "discovery_source",
    "discovery_query",
]

EVIDENCE_CSV_COLUMNS = [
    "provider_id",
    "domain",
    "evidence_kind",
    "evidence_type",
    "score_contribution",
    "matched_text",
    "context_excerpt",
    "source_url",
    "page_title",
    "origin",
    "document_page",
    "extracted_at",
]


def _delimiter() -> str:
    return get_app_settings().get("export", {}).get("csv_delimiter_within_field", " | ")


def _join(values: list[str]) -> str:
    delimiter = _delimiter()
    return delimiter.join(v for v in values if v)


def provider_to_row(provider: ProviderRecord) -> dict:
    delimiter = _delimiter()
    return {
        "provider_id": provider.provider_id,
        "provider_name": provider.trading_name or "",
        "legal_name": provider.legal_name or "",
        "abn": provider.abn or "",
        "abn_valid": provider.abn_valid if provider.abn_valid is not None else "",
        "abn_lookup_confirmed": provider.abn_lookup_confirmed if provider.abn_lookup_confirmed is not None else "",
        "domain": provider.domain,
        "website_url": provider.website_url,
        "phone": provider.phone or "",
        "email": provider.email or "",
        "address": provider.address or "",
        "states": delimiter.join(provider.states),
        "service_locations": delimiter.join(provider.service_locations),
        "sil_score": provider.sil_score,
        "sil_classification": provider.sil_classification,
        "sil_confidence": provider.sil_confidence,
        "sil_evidence_summary": _join([e.matched_text for e in provider.sil_evidence]),
        "sil_evidence_urls": _join(sorted({e.source_url for e in provider.sil_evidence})),
        "registration_claim_status": provider.registration_claim_status,
        "registration_claim_confidence": provider.registration_claim_confidence,
        "registration_claim_text": _join([e.matched_text for e in provider.registration_evidence]),
        "registration_claim_urls": _join(sorted({e.source_url for e in provider.registration_evidence})),
        "register_match_status": provider.register_match_status,
        "register_registration_status": provider.register_registration_status or "",
        "register_entity_name": provider.register_entity_name or "",
        "register_abn": provider.register_abn or "",
        "register_match_confidence": provider.register_match_confidence if provider.register_match_confidence is not None else "",
        "register_snapshot_date": provider.register_snapshot_date.isoformat() if provider.register_snapshot_date else "",
        "automated_segment": provider.automated_segment,
        "manual_review_status": provider.manual_review_status,
        "last_website_check": provider.last_website_check.isoformat() if provider.last_website_check else "",
        "last_register_check": provider.last_register_check.isoformat() if provider.last_register_check else "",
        "discovery_source": provider.discovery_source or "",
        "discovery_query": provider.discovery_query or "",
    }


def evidence_to_rows(provider: ProviderRecord) -> list[dict]:
    rows = []
    for kind, items in (("SIL", provider.sil_evidence), ("REGISTRATION", provider.registration_evidence)):
        item: EvidenceItem
        for item in items:
            rows.append(
                {
                    "provider_id": provider.provider_id,
                    "domain": provider.domain,
                    "evidence_kind": kind,
                    "evidence_type": item.evidence_type,
                    "score_contribution": item.score_contribution if item.score_contribution is not None else "",
                    "matched_text": item.matched_text,
                    "context_excerpt": item.context_excerpt,
                    "source_url": item.source_url,
                    "page_title": item.page_title or "",
                    "origin": item.origin,
                    "document_page": item.document_page if item.document_page is not None else "",
                    "extracted_at": item.extracted_at.isoformat() if item.extracted_at else "",
                }
            )
    return rows


def export_providers_csv(providers: list[ProviderRecord], output_path: str | Path) -> int:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PROVIDER_CSV_COLUMNS)
        writer.writeheader()
        for provider in providers:
            writer.writerow(provider_to_row(provider))
    return len(providers)


def export_evidence_csv(providers: list[ProviderRecord], output_path: str | Path) -> int:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=EVIDENCE_CSV_COLUMNS)
        writer.writeheader()
        for provider in providers:
            for row in evidence_to_rows(provider):
                writer.writerow(row)
                row_count += 1
    return row_count
