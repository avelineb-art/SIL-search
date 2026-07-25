from __future__ import annotations

from dashboard.data import (
    ProviderFilters,
    diff_page_text,
    get_page_snapshots,
    get_page_urls,
    get_provider_detail,
    list_providers,
    mark_false_positive,
    record_review_decision,
)
from sil_research.database import CrawlRun, Page, Provider, SilEvidence
from sil_research.models import ManualReviewStatus


def _make_provider(db_session, **overrides) -> Provider:
    defaults = dict(
        provider_id="example.com.au",
        domain="example.com.au",
        website_url="https://example.com.au/",
        trading_name="Example Provider",
        legal_name="Example Provider Pty Ltd",
        states=["NSW"],
        sil_score=10,
        sil_classification="STRONG_SIL_EVIDENCE",
        registration_claim_status="NO_REGISTERED_CLAIM_FOUND",
        register_match_status="NO_CONFIDENT_MATCH",
        automated_segment="SIL_PROVIDER_NO_PUBLIC_REGISTRATION_STATEMENT_REGISTER_STATUS_UNCONFIRMED",
        manual_review_status="PENDING",
    )
    defaults.update(overrides)
    provider = Provider(**defaults)
    db_session.add(provider)
    db_session.commit()
    return provider


def test_list_providers_with_no_filters_returns_all(db_session):
    _make_provider(db_session)
    _make_provider(db_session, provider_id="other.com.au", domain="other.com.au", website_url="https://other.com.au/", states=["VIC"])

    rows = list_providers(db_session)
    assert {r["provider_id"] for r in rows} == {"example.com.au", "other.com.au"}


def test_list_providers_filters_by_state(db_session):
    _make_provider(db_session, states=["NSW"])
    _make_provider(db_session, provider_id="other.com.au", domain="other.com.au", website_url="https://other.com.au/", states=["VIC"])

    rows = list_providers(db_session, ProviderFilters(state="VIC"))
    assert [r["provider_id"] for r in rows] == ["other.com.au"]


def test_list_providers_filters_by_sil_classification(db_session):
    _make_provider(db_session, sil_classification="STRONG_SIL_EVIDENCE")
    _make_provider(
        db_session,
        provider_id="other.com.au",
        domain="other.com.au",
        website_url="https://other.com.au/",
        sil_classification="POSSIBLE_SIL_PROVIDER",
    )

    rows = list_providers(db_session, ProviderFilters(sil_classification="POSSIBLE_SIL_PROVIDER"))
    assert [r["provider_id"] for r in rows] == ["other.com.au"]


def test_list_providers_filters_by_min_sil_score(db_session):
    _make_provider(db_session, sil_score=10)
    _make_provider(db_session, provider_id="weak.com.au", domain="weak.com.au", website_url="https://weak.com.au/", sil_score=2)

    rows = list_providers(db_session, ProviderFilters(min_sil_score=5))
    assert [r["provider_id"] for r in rows] == ["example.com.au"]


def test_list_providers_filters_by_automated_segment(db_session):
    _make_provider(db_session, automated_segment="SIL_SERVICE_CLASSIFICATION_REQUIRES_REVIEW")
    _make_provider(db_session, provider_id="other.com.au", domain="other.com.au", website_url="https://other.com.au/", automated_segment="UNSEGMENTED")

    rows = list_providers(db_session, ProviderFilters(automated_segment="UNSEGMENTED"))
    assert [r["provider_id"] for r in rows] == ["other.com.au"]


def test_list_providers_keyword_search_matches_trading_name(db_session):
    _make_provider(db_session, trading_name="Sunrise Living")
    _make_provider(db_session, provider_id="other.com.au", domain="other.com.au", website_url="https://other.com.au/", trading_name="Beacon Support")

    rows = list_providers(db_session, ProviderFilters(keyword="sunrise"))
    assert [r["provider_id"] for r in rows] == ["example.com.au"]


def test_get_provider_detail_returns_none_for_unknown_provider(db_session):
    assert get_provider_detail(db_session, "nope.com.au") is None


def test_get_provider_detail_includes_evidence(db_session):
    provider = _make_provider(db_session)
    db_session.add(
        SilEvidence(
            provider_id=provider.provider_id,
            evidence_category="exact_sil_phrase",
            matched_text="supported independent living",
            context_excerpt="...",
            score_contribution=5,
            source_url="https://example.com.au/services",
            evidence_hash="abc123",
        )
    )
    db_session.commit()

    detail = get_provider_detail(db_session, provider.provider_id)
    assert detail is not None
    assert len(detail.sil_evidence) == 1
    assert detail.sil_evidence[0].matched_text == "supported independent living"


def test_record_review_decision_updates_provider_and_creates_history_row(db_session):
    provider = _make_provider(db_session)
    record_review_decision(db_session, provider.provider_id, reviewer="Aveline", decision=ManualReviewStatus.CONFIRMED_SIL_PROVIDER, notes="Looks right.")
    db_session.commit()

    refreshed = db_session.get(Provider, provider.provider_id)
    assert refreshed.manual_review_status == ManualReviewStatus.CONFIRMED_SIL_PROVIDER
    assert refreshed.reviewer_notes == "Looks right."

    detail = get_provider_detail(db_session, provider.provider_id)
    assert len(detail.review_decisions) == 1
    assert detail.review_decisions[0].reviewer == "Aveline"
    assert detail.review_decisions[0].prior_automated_segment == provider.automated_segment


def test_mark_false_positive_uses_not_a_sil_provider_status(db_session):
    provider = _make_provider(db_session)
    mark_false_positive(db_session, provider.provider_id, reviewer="Aveline")
    db_session.commit()

    refreshed = db_session.get(Provider, provider.provider_id)
    assert refreshed.manual_review_status == ManualReviewStatus.NOT_A_SIL_PROVIDER


def test_record_review_decision_raises_for_unknown_provider(db_session):
    import pytest

    with pytest.raises(ValueError):
        record_review_decision(db_session, "nope.com.au", reviewer="Aveline", decision=ManualReviewStatus.CONFIRMED_SIL_PROVIDER)


def test_get_page_urls_and_snapshots_support_change_comparison(db_session):
    provider = _make_provider(db_session)
    run1 = CrawlRun(provider_id=provider.provider_id, pages_crawled=1, status="COMPLETED")
    db_session.add(run1)
    db_session.flush()
    db_session.add(
        Page(provider_id=provider.provider_id, crawl_run_id=run1.run_id, url=provider.website_url, canonical_url=provider.website_url, http_status=200)
    )
    run2 = CrawlRun(provider_id=provider.provider_id, pages_crawled=1, status="COMPLETED")
    db_session.add(run2)
    db_session.flush()
    db_session.add(
        Page(provider_id=provider.provider_id, crawl_run_id=run2.run_id, url=provider.website_url, canonical_url=provider.website_url, http_status=200)
    )
    db_session.commit()

    urls = get_page_urls(db_session, provider.provider_id)
    assert urls == [provider.website_url]

    snapshots = get_page_snapshots(db_session, provider.provider_id, provider.website_url)
    assert len(snapshots) == 2
    assert snapshots[0].crawl_run_id == run1.run_id
    assert snapshots[1].crawl_run_id == run2.run_id


def test_diff_page_text_shows_added_and_removed_lines():
    diff = diff_page_text("Line one\nLine two", "Line one\nLine three", old_label="2026-01-01", new_label="2026-07-01")
    assert "-Line two" in diff
    assert "+Line three" in diff
    assert "2026-01-01" in diff
    assert "2026-07-01" in diff
