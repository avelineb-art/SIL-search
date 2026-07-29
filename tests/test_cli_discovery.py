from __future__ import annotations

from sil_research.cli import _run_discovery
from sil_research.database import Provider, SearchQuery
from sil_research.discovery.base import SearchProvider, SearchProviderError, SearchQuotaExceededError, SearchResult
from sil_research.discovery.query_generator import CandidateQuery


class StubSearchProvider(SearchProvider):
    """Returns one distinct, ordinary provider result per call - no exclusion
    rules (job boards, PDFs, social media, etc.) are exercised here since
    this test is only about _run_discovery's pacing and bookkeeping.
    """

    def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
        domain = query.replace(" ", "-").lower()
        return [
            SearchResult(
                url=f"https://{domain}.com.au/",
                title=domain,
                snippet="Supported independent living provider.",
                rank=1,
                query=query,
            )
        ]


def _batch(n: int) -> list[CandidateQuery]:
    return [
        CandidateQuery(phrase="SIL NDIS", location=f"Location{i}", query_text=f"query {i}")
        for i in range(n)
    ]


def test_run_discovery_sleeps_between_queries_but_not_before_the_first(db_session):
    sleeps: list[float] = []
    providers_created = _run_discovery(
        db_session, _batch(3), StubSearchProvider(), delay_seconds=2.5, sleep_fn=sleeps.append
    )

    assert sleeps == [2.5, 2.5]
    assert providers_created == 3
    assert db_session.query(SearchQuery).count() == 3
    assert db_session.query(Provider).count() == 3


def test_run_discovery_stops_on_quota_exceeded_without_sleeping_again():
    class QuotaExceededProvider(SearchProvider):
        def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
            raise SearchQuotaExceededError("daily budget exhausted")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sil_research.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        sleeps: list[float] = []
        providers_created = _run_discovery(
            session, _batch(5), QuotaExceededProvider(), delay_seconds=1.0, sleep_fn=sleeps.append
        )
        assert providers_created == 0
        assert sleeps == []
    finally:
        session.close()
        engine.dispose()


def test_run_discovery_continues_past_a_single_provider_error(db_session):
    calls = {"n": 0}

    class FlakyProvider(SearchProvider):
        def search(self, query: str, start: int = 0, limit: int = 10) -> list[SearchResult]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise SearchProviderError("transient failure")
            return [
                SearchResult(
                    url="https://example.com.au/",
                    title="Example",
                    snippet="Supported independent living provider.",
                    rank=1,
                    query=query,
                )
            ]

    providers_created = _run_discovery(
        db_session, _batch(2), FlakyProvider(), delay_seconds=0, sleep_fn=lambda _: None
    )
    assert providers_created == 1
    assert calls["n"] == 2
