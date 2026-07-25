from __future__ import annotations

from datetime import datetime

from sil_research.cli import _classify_provider
from sil_research.database import CrawlRun, Page, PageText, Provider


def _make_provider(db_session, provider_id="example.com.au") -> Provider:
    provider = Provider(provider_id=provider_id, domain=provider_id, website_url=f"https://{provider_id}/", crawl_status="CRAWLED")
    db_session.add(provider)
    db_session.commit()
    return provider


def _add_crawl_run_with_page(db_session, provider_id, visible_text) -> CrawlRun:
    run = CrawlRun(provider_id=provider_id, started_at=datetime.utcnow(), finished_at=datetime.utcnow(), pages_crawled=1, status="COMPLETED")
    db_session.add(run)
    db_session.flush()
    page = Page(
        provider_id=provider_id,
        crawl_run_id=run.run_id,
        url=f"https://{provider_id}/",
        canonical_url=f"https://{provider_id}/",
        http_status=200,
        page_title="Home",
        fetched_at=datetime.utcnow(),
        page_type="general",
    )
    db_session.add(page)
    db_session.flush()
    db_session.add(PageText(page_id=page.page_id, visible_text=visible_text, headings=[], footer_text="", structured_data={}))
    db_session.commit()
    return run


def test_recrawling_the_same_url_does_not_violate_a_uniqueness_constraint(db_session):
    """Page is intentionally not unique on (provider_id, canonical_url) -
    each crawl/recrawl keeps its own row, tagged by crawl_run_id, so crawl
    history accumulates instead of crashing on the second crawl of a URL.
    """
    provider = _make_provider(db_session)
    _add_crawl_run_with_page(db_session, provider.provider_id, "First crawl content.")
    _add_crawl_run_with_page(db_session, provider.provider_id, "Second crawl content, changed.")

    pages = db_session.query(Page).filter_by(provider_id=provider.provider_id).all()
    assert len(pages) == 2
    assert {p.crawl_run_id for p in pages} == {1, 2}


def test_classify_provider_only_scores_the_latest_crawl_run(db_session):
    provider = _make_provider(db_session)
    _add_crawl_run_with_page(db_session, provider.provider_id, "We only provide support coordination for NDIS participants.")
    _add_crawl_run_with_page(
        db_session,
        provider.provider_id,
        "We provide Supported Independent Living (SIL) with 24/7 support in shared homes.",
    )

    _classify_provider(db_session, provider)
    db_session.commit()

    # Only the second (latest) crawl's content should have contributed -
    # the first crawl's support-coordination-only page must not still be
    # pulling the score down.
    assert provider.sil_score > 0
    assert provider.sil_classification != "INSUFFICIENT_SIL_EVIDENCE"
