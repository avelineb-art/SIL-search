"""Data-access layer for the review dashboard.

Deliberately Streamlit-free: every function here takes/returns plain
Python objects (dicts, dataclasses) and can be unit-tested with an
in-memory database, exactly like the CLI's own logic. `app.py` is thin
rendering on top of this module - if a Streamlit run ever behaves
unexpectedly, the underlying query/action logic can still be checked here
without touching the UI.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sil_research.cli import _classify_provider
from sil_research.cli import recrawl as _cli_recrawl
from sil_research.database import (
    CrawlRun,
    Document,
    ErrorRecord,
    ExportRun,
    Page,
    Provider,
    ProviderRegisterMatch,
    RegistrationEvidence,
    ReviewDecision,
    SilEvidence,
    session_scope,
)
from sil_research.models import ManualReviewStatus


@dataclass
class ProviderFilters:
    state: str | None = None
    sil_classification: str | None = None
    registration_claim_status: str | None = None
    register_match_status: str | None = None
    automated_segment: str | None = None
    min_sil_score: int | None = None
    keyword: str | None = None


def list_providers(session: Session, filters: ProviderFilters | None = None) -> list[dict]:
    """Provider-table rows for the dashboard's main list view."""
    filters = filters or ProviderFilters()
    stmt = select(Provider)

    if filters.sil_classification:
        stmt = stmt.where(Provider.sil_classification == filters.sil_classification)
    if filters.registration_claim_status:
        stmt = stmt.where(Provider.registration_claim_status == filters.registration_claim_status)
    if filters.register_match_status:
        stmt = stmt.where(Provider.register_match_status == filters.register_match_status)
    if filters.automated_segment:
        stmt = stmt.where(Provider.automated_segment == filters.automated_segment)
    if filters.min_sil_score is not None:
        stmt = stmt.where(Provider.sil_score >= filters.min_sil_score)

    providers = list(session.execute(stmt).scalars())

    if filters.state:
        providers = [p for p in providers if filters.state in (p.states or [])]
    if filters.keyword:
        needle = filters.keyword.lower()
        providers = [
            p
            for p in providers
            if needle in (p.trading_name or "").lower()
            or needle in (p.legal_name or "").lower()
            or needle in (p.domain or "").lower()
        ]

    return [
        {
            "provider_id": p.provider_id,
            "trading_name": p.trading_name,
            "legal_name": p.legal_name,
            "domain": p.domain,
            "states": ", ".join(p.states or []),
            "sil_score": p.sil_score,
            "sil_classification": p.sil_classification,
            "registration_claim_status": p.registration_claim_status,
            "register_match_status": p.register_match_status,
            "automated_segment": p.automated_segment,
            "manual_review_status": p.manual_review_status,
            "last_website_check": p.last_website_check,
        }
        for p in providers
    ]


@dataclass
class ProviderDetail:
    provider: Provider
    sil_evidence: list[SilEvidence] = field(default_factory=list)
    registration_evidence: list[RegistrationEvidence] = field(default_factory=list)
    register_matches: list[ProviderRegisterMatch] = field(default_factory=list)
    crawl_runs: list[CrawlRun] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    errors: list[ErrorRecord] = field(default_factory=list)
    review_decisions: list[ReviewDecision] = field(default_factory=list)


def get_provider_detail(session: Session, provider_id: str) -> ProviderDetail | None:
    provider = session.get(Provider, provider_id)
    if provider is None:
        return None
    return ProviderDetail(
        provider=provider,
        sil_evidence=list(session.execute(select(SilEvidence).where(SilEvidence.provider_id == provider_id)).scalars()),
        registration_evidence=list(
            session.execute(select(RegistrationEvidence).where(RegistrationEvidence.provider_id == provider_id)).scalars()
        ),
        register_matches=list(
            session.execute(
                select(ProviderRegisterMatch).where(ProviderRegisterMatch.provider_id == provider_id).order_by(ProviderRegisterMatch.matched_at.desc())
            ).scalars()
        ),
        crawl_runs=list(
            session.execute(select(CrawlRun).where(CrawlRun.provider_id == provider_id).order_by(CrawlRun.started_at.desc())).scalars()
        ),
        documents=list(
            session.execute(select(Document).where(Document.provider_id == provider_id).order_by(Document.fetched_at.desc())).scalars()
        ),
        errors=list(
            session.execute(select(ErrorRecord).where(ErrorRecord.provider_id == provider_id).order_by(ErrorRecord.occurred_at.desc())).scalars()
        ),
        review_decisions=list(
            session.execute(select(ReviewDecision).where(ReviewDecision.provider_id == provider_id).order_by(ReviewDecision.decided_at.desc())).scalars()
        ),
    )


def get_page_urls(session: Session, provider_id: str) -> list[str]:
    """Distinct canonical URLs ever crawled for a provider, for the
    change-comparison picker.
    """
    rows = session.execute(select(Page.canonical_url).where(Page.provider_id == provider_id).distinct()).all()
    return sorted({row[0] for row in rows})


def get_page_snapshots(session: Session, provider_id: str, canonical_url: str) -> list[Page]:
    """All historical fetches of one URL, oldest first, each tagged with
    its crawl run - the raw material for the "compare website changes
    between crawl dates" feature.
    """
    return list(
        session.execute(
            select(Page)
            .where(Page.provider_id == provider_id, Page.canonical_url == canonical_url)
            .order_by(Page.fetched_at.asc())
        ).scalars()
    )


def diff_page_text(old_text: str | None, new_text: str | None, old_label: str = "before", new_label: str = "after") -> str:
    """Unified diff between two crawl snapshots of the same page's visible text."""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    return "\n".join(
        difflib.unified_diff(old_lines, new_lines, fromfile=old_label, tofile=new_label, lineterm="")
    )


def record_review_decision(
    session: Session,
    provider_id: str,
    reviewer: str,
    decision: str,
    notes: str | None = None,
) -> ReviewDecision:
    """Records a manual-review decision and updates the provider's current
    status - the original automated classification is left untouched
    (it's still visible on the provider record and in evidence tables).
    """
    provider = session.get(Provider, provider_id)
    if provider is None:
        raise ValueError(f"No provider found for {provider_id}")

    review = ReviewDecision(
        provider_id=provider_id,
        reviewer=reviewer or None,
        decision=decision,
        notes=notes,
        prior_automated_segment=provider.automated_segment,
        decided_at=datetime.utcnow(),
    )
    session.add(review)
    provider.manual_review_status = decision
    if notes:
        provider.reviewer_notes = notes
    return review


def mark_false_positive(session: Session, provider_id: str, reviewer: str, notes: str | None = None) -> ReviewDecision:
    """Convenience wrapper for the dashboard's one-click "mark false
    positive" action - maps to the closest decision in the build spec's
    fixed decision list (there is no separate FALSE_POSITIVE status).
    """
    combined_notes = notes or "Marked as a false positive from the dashboard."
    return record_review_decision(session, provider_id, reviewer, ManualReviewStatus.NOT_A_SIL_PROVIDER, combined_notes)


def recrawl_provider(domain: str) -> None:
    """Triggers a fresh crawl of one provider's site, in its own DB
    session/transaction (the CLI's `recrawl` command is a plain function,
    so this just calls it directly rather than duplicating its logic).
    """
    _cli_recrawl(domain=domain)


def reclassify_provider(domain: str) -> None:
    """Re-runs both classifiers for one provider against its latest crawl,
    without recrawling first.
    """
    with session_scope() as session:
        provider = session.get(Provider, domain)
        if provider is None:
            raise ValueError(f"No provider found for {domain}")
        _classify_provider(session, provider)


def run_full_csv_export() -> str:
    """Runs the same full provider+evidence CSV export as
    `python -m sil_research export --format csv` and returns the path of
    the resulting provider CSV.
    """
    from sil_research.cli import export_cmd

    export_cmd(format="csv")
    with session_scope() as session:
        latest = session.execute(select(ExportRun).where(ExportRun.format == "csv").order_by(ExportRun.export_id.desc()).limit(1)).scalar_one_or_none()
        return latest.file_path if latest else ""
