"""Command-line interface. See README.md for full usage examples.

This module is the composition root: it wires the otherwise-independent
discovery / crawling / classification / export modules together against
the database. Business logic (scoring, extraction, matching) intentionally
lives in those modules, not here, so it stays unit-testable without a CLI
or a database.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from urllib.parse import urlparse

import typer
from sqlalchemy import select

from sil_research.classification.registration_classifier import classify_registration
from sil_research.classification.scoring import TextSource, classify_page_type, evidence_hash
from sil_research.classification.sil_classifier import classify_sil
from sil_research.config import get_settings, resolve_data_path
from sil_research.crawling.crawler import Crawler, CrawlOutcome
from sil_research.database import (
    CrawlRun,
    Document,
    Domain,
    ErrorRecord,
    ExportRun,
    Page,
    PageText,
    Provider,
    ProviderRegisterMatch,
    ProviderRegisterSnapshot,
    RegisterEntry,
    RegistrationEvidence,
    SearchQuery,
    SearchResult,
    SilEvidence,
    init_db,
    session_scope,
)
from sil_research.discovery.base import SearchProvider, SearchProviderError, SearchQuotaExceededError
from sil_research.discovery.domain_filter import canonical_domain, classify_domain
from sil_research.discovery.job_board import extract_employer_from_job_ad
from sil_research.discovery.query_generator import QueryGenerator
from sil_research.discovery.search_provider import (
    BraveSearchProvider,
    GoogleCSEProvider,
    MockSearchProvider,
    SerpApiProvider,
)
from sil_research.export.csv_export import export_evidence_csv, export_providers_csv
from sil_research.export.json_export import export_providers_json
from sil_research.extraction.abn import find_abns
from sil_research.extraction.abn_lookup import AbnLookupError, AbrJsonAbnLookupClient, CachingAbnLookupClient
from sil_research.extraction.contact import extract_general_email, extract_phone_numbers
from sil_research.extraction.locations import extract_service_locations, extract_states
from sil_research.extraction.organisation import find_copyright_name, find_legal_name_candidates, normalise_name
from sil_research.logging_config import configure_logging
from sil_research.models import EvidenceItem, ProviderRecord
from sil_research.register.base import RegisterEntryData, RegisterImportError
from sil_research.register.importer import import_register_file
from sil_research.register.matcher import match_provider
from sil_research.review.workflow import compute_automated_segment

app = typer.Typer(help="Compliant SIL provider lead-research CLI", add_completion=False)


def _build_search_provider(settings) -> SearchProvider:
    if settings.sil_search_provider == "brave":
        if not settings.brave_search_api_key:
            typer.echo("SIL_SEARCH_PROVIDER=brave but BRAVE_SEARCH_API_KEY is not set.")
            raise typer.Exit(1)
        return BraveSearchProvider(
            api_key=settings.brave_search_api_key,
            country=settings.brave_country,
            search_lang=settings.brave_search_lang,
        )
    if settings.sil_search_provider == "serpapi":
        if not settings.serpapi_api_key:
            typer.echo("SIL_SEARCH_PROVIDER=serpapi but SERPAPI_API_KEY is not set.")
            raise typer.Exit(1)
        return SerpApiProvider(
            api_key=settings.serpapi_api_key,
            google_domain=settings.serpapi_google_domain,
            country=settings.serpapi_country,
            language=settings.serpapi_language,
        )
    if settings.sil_search_provider == "google_cse":
        if not settings.google_cse_api_key or not settings.google_cse_cx:
            typer.echo("SIL_SEARCH_PROVIDER=google_cse but GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX are not set.")
            raise typer.Exit(1)
        return GoogleCSEProvider(api_key=settings.google_cse_api_key, cx=settings.google_cse_cx)
    return MockSearchProvider()


def _to_provider_record(
    provider_row: Provider,
    sil_evidence: list[EvidenceItem] | None = None,
    registration_evidence: list[EvidenceItem] | None = None,
) -> ProviderRecord:
    return ProviderRecord(
        provider_id=provider_row.provider_id,
        trading_name=provider_row.trading_name,
        legal_name=provider_row.legal_name,
        normalised_trading_name=provider_row.normalised_trading_name,
        normalised_legal_name=provider_row.normalised_legal_name,
        abn=provider_row.abn,
        abn_valid=provider_row.abn_valid,
        abn_lookup_confirmed=provider_row.abn_lookup_confirmed,
        acn=provider_row.acn,
        domain=provider_row.domain,
        website_url=provider_row.website_url,
        phone=provider_row.phone,
        email=provider_row.email,
        address=provider_row.address,
        states=list(provider_row.states or []),
        service_locations=list(provider_row.service_locations or []),
        sil_score=provider_row.sil_score,
        sil_classification=provider_row.sil_classification,
        sil_confidence=provider_row.sil_confidence,
        sil_evidence=sil_evidence or [],
        registration_claim_status=provider_row.registration_claim_status,
        registration_claim_confidence=provider_row.registration_claim_confidence,
        registration_evidence=registration_evidence or [],
        register_match_status=provider_row.register_match_status,
        register_registration_status=provider_row.register_registration_status,
        register_match_confidence=provider_row.register_match_confidence,
        register_entity_name=provider_row.register_entity_name,
        register_abn=provider_row.register_abn,
        register_snapshot_date=provider_row.register_snapshot_date,
        discovery_query=provider_row.discovery_query,
        discovery_source=provider_row.discovery_source,
        last_website_check=provider_row.last_website_check,
        last_register_check=provider_row.last_register_check,
        automated_segment=provider_row.automated_segment,
        manual_review_status=provider_row.manual_review_status,
        reviewer_notes=provider_row.reviewer_notes,
    )


def _latest_crawl_run_id(session, provider_id: str) -> int | None:
    return session.execute(
        select(CrawlRun.run_id).where(CrawlRun.provider_id == provider_id).order_by(CrawlRun.run_id.desc()).limit(1)
    ).scalar_one_or_none()


def _update_identity_fields(provider_row: Provider, outcome: CrawlOutcome) -> None:
    """Best-effort organisation-identity extraction from everything crawled
    for this provider in this run. Values are overwritten on each crawl so
    the record always reflects the latest snapshot; ABN Lookup confirmation
    (Stage 3) is authoritative over anything extracted here.
    """
    text_parts: list[str] = []
    footer_parts: list[str] = []
    for page in outcome.pages:
        if page.parsed:
            if page.parsed.visible_text:
                text_parts.append(page.parsed.visible_text)
            if page.parsed.footer_text:
                footer_parts.append(page.parsed.footer_text)
    for doc in outcome.documents:
        if doc.parsed and not doc.parsed.extraction_failed:
            text_parts.append(doc.parsed.full_text)

    combined_text = "\n".join(text_parts)
    footer_text = "\n".join(footer_parts)

    abn_matches = find_abns(combined_text)
    valid = [m for m in abn_matches if m.valid_checksum]
    if valid:
        provider_row.abn, provider_row.abn_valid = valid[0].digits, True
    elif abn_matches:
        provider_row.abn, provider_row.abn_valid = abn_matches[0].digits, False

    phones = extract_phone_numbers(combined_text)
    if phones:
        provider_row.phone = phones[0]

    email = extract_general_email(combined_text, domain=provider_row.domain)
    if email:
        provider_row.email = email

    states = extract_states(combined_text)
    if states:
        provider_row.states = states

    service_locations = extract_service_locations(combined_text)
    if service_locations:
        provider_row.service_locations = sorted({loc.matched_text for loc in service_locations})

    copyright_name = find_copyright_name(footer_text)
    if copyright_name:
        provider_row.legal_name = copyright_name
        provider_row.normalised_legal_name = normalise_name(copyright_name)
    elif not provider_row.legal_name:
        candidates = find_legal_name_candidates(combined_text, source="page_text")
        if candidates:
            provider_row.legal_name = candidates[0].name
            provider_row.normalised_legal_name = normalise_name(candidates[0].name)

    homepage = next(
        (p for p in outcome.pages if p.parsed and urlparse(p.canonical_url).path in ("", "/")),
        outcome.pages[0] if outcome.pages else None,
    )
    if homepage and homepage.parsed and homepage.parsed.title and not provider_row.trading_name:
        provider_row.trading_name = homepage.parsed.title
        provider_row.normalised_trading_name = normalise_name(homepage.parsed.title)


def _run_discovery(session, batch, provider: SearchProvider, delay_seconds: float, sleep_fn=time.sleep) -> int:
    """Executes a batch of discovery queries against `provider`, one at a
    time with `delay_seconds` between requests.

    The delay matters: firing every query back-to-back with no pacing (as
    an earlier version of this function did) can trip a search API's burst
    rate limit, and the *first* location/phrase combinations in the batch -
    typically a state capital or the state name itself, i.e. exactly the
    highest-value queries - are the ones that pay for it, silently coming
    back empty or erroring while later, less important queries succeed
    once the rate limit window resets. A fixed inter-query delay avoids
    that failure mode entirely rather than trying to detect it after the
    fact.
    """
    providers_created = 0
    for index, candidate in enumerate(batch):
        if index > 0:
            sleep_fn(delay_seconds)

        query_row = SearchQuery(
            query_text=candidate.query_text,
            phrase=candidate.phrase,
            location=candidate.location,
            run_at=datetime.utcnow(),
        )
        session.add(query_row)
        session.flush()

        try:
            results = provider.search(candidate.query_text, limit=10)
        except SearchQuotaExceededError as exc:
            session.add(ErrorRecord(stage="discovery", error_type="SearchQuotaExceededError", message=str(exc)))
            typer.echo(f"Stopping: {exc}")
            break
        except SearchProviderError as exc:
            session.add(ErrorRecord(stage="discovery", error_type="SearchProviderError", message=str(exc)))
            continue

        query_row.result_count = len(results)

        for result in results:
            if session.execute(select(SearchResult.result_id).where(SearchResult.url == result.url)).scalar_one_or_none():
                continue

            decision = classify_domain(result.url)
            target_url, target_domain, discovery_source = result.url, decision.domain, "search_api"

            if decision.is_job_board or decision.is_generic_directory:
                employer = extract_employer_from_job_ad(result.snippet or "")
                if employer.employer_website:
                    target_url = employer.employer_website
                    target_domain = canonical_domain(target_url)
                    discovery_source = "job_board" if decision.is_job_board else "directory"
                else:
                    session.add(
                        SearchResult(
                            query_id=query_row.query_id,
                            url=result.url,
                            title=result.title,
                            snippet=result.snippet,
                            rank=result.rank,
                            canonical_domain=decision.domain,
                            excluded=True,
                            exclusion_reason="listing domain with no extractable employer website",
                        )
                    )
                    continue
            elif decision.excluded:
                session.add(
                    SearchResult(
                        query_id=query_row.query_id,
                        url=result.url,
                        title=result.title,
                        snippet=result.snippet,
                        rank=result.rank,
                        canonical_domain=decision.domain,
                        excluded=True,
                        exclusion_reason=decision.reason,
                    )
                )
                continue

            session.add(
                SearchResult(
                    query_id=query_row.query_id,
                    url=result.url,
                    title=result.title,
                    snippet=result.snippet,
                    rank=result.rank,
                    canonical_domain=target_domain,
                    excluded=False,
                )
            )

            if not session.execute(select(Domain).where(Domain.canonical_domain == target_domain)).scalar_one_or_none():
                session.add(Domain(canonical_domain=target_domain))

            if session.get(Provider, target_domain) is None:
                session.add(
                    Provider(
                        provider_id=target_domain,
                        domain=target_domain,
                        website_url=target_url,
                        discovery_query=candidate.query_text,
                        discovery_source=discovery_source,
                        crawl_status="PENDING",
                    )
                )
                providers_created += 1

    return providers_created


@app.command()
def discover(
    state: str = typer.Option(None, "--state", help="Restrict to one state/territory (e.g. QLD, NSW)."),
    limit: int = typer.Option(None, "--limit", help="Override the configured daily query budget."),
) -> None:
    """Generate and run search queries, filter/dedupe results, create pending provider records."""
    configure_logging()
    init_db()
    settings = get_settings()
    generator = QueryGenerator()
    provider = _build_search_provider(settings)

    with session_scope() as session:
        cutoff = generator.freshness_cutoff()
        rows = session.execute(select(SearchQuery.phrase, SearchQuery.location).where(SearchQuery.run_at >= cutoff)).all()
        already_run = {(phrase, location) for phrase, location in rows}

        batch = generator.next_batch(already_run=already_run, budget=limit, state_filter=state)
        typer.echo(f"Running {len(batch)} quer{'y' if len(batch) == 1 else 'ies'}...")

        providers_created = _run_discovery(session, batch, provider, settings.sil_discovery_delay_seconds)

        typer.echo(f"Discovered {providers_created} new candidate provider domain(s).")


def _run_crawl(session, providers: list[Provider], concurrency: int, settings) -> None:
    crawler = Crawler(
        user_agent=settings.sil_crawler_user_agent,
        delay_seconds=settings.sil_crawl_delay_seconds,
        max_pages_per_domain=settings.sil_crawl_max_pages_per_domain,
        timeout_seconds=settings.sil_crawl_request_timeout_seconds,
    )
    website_urls = {p.website_url: p for p in providers}
    outcomes = crawler.crawl_many(list(website_urls.keys()), concurrency=concurrency)

    for website_url, outcome in outcomes.items():
        provider_row = website_urls[website_url]
        run = CrawlRun(provider_id=provider_row.provider_id, started_at=datetime.utcnow())
        session.add(run)
        session.flush()  # assigns run.run_id, needed as a FK on each Page row below

        for crawled_page in outcome.pages:
            page_title = crawled_page.parsed.title if crawled_page.parsed else None
            page_type = classify_page_type(crawled_page.url, page_title)
            page_row = Page(
                provider_id=provider_row.provider_id,
                crawl_run_id=run.run_id,
                url=crawled_page.url,
                canonical_url=crawled_page.canonical_url,
                http_status=crawled_page.http_status,
                page_title=page_title,
                meta_description=crawled_page.parsed.meta_description if crawled_page.parsed else None,
                fetched_at=crawled_page.fetched_at,
                error=crawled_page.error,
                page_type=page_type,
            )
            session.add(page_row)
            session.flush()
            if crawled_page.parsed:
                session.add(
                    PageText(
                        page_id=page_row.page_id,
                        visible_text=crawled_page.parsed.visible_text,
                        headings=crawled_page.parsed.headings,
                        footer_text=crawled_page.parsed.footer_text,
                        structured_data=(crawled_page.parsed.structured_data[0] if crawled_page.parsed.structured_data else {}),
                    )
                )
            if crawled_page.error:
                session.add(
                    ErrorRecord(
                        provider_id=provider_row.provider_id,
                        stage="crawl",
                        error_type="PageFetchError",
                        message=crawled_page.error,
                        context={"url": crawled_page.url},
                    )
                )

        for doc in outcome.documents:
            failed = bool(doc.error) or bool(doc.parsed and doc.parsed.extraction_failed)
            doc_row = Document(
                provider_id=provider_row.provider_id,
                crawl_run_id=run.run_id,
                source_url=doc.source_url,
                doc_type=doc.parsed.doc_type if doc.parsed else None,
                extraction_status="FAILED" if failed else "OK",
                extracted_text=doc.parsed.full_text if (doc.parsed and not failed) else None,
                error=doc.error or (doc.parsed.error if doc.parsed else None),
            )
            session.add(doc_row)
            if failed:
                session.add(
                    ErrorRecord(
                        provider_id=provider_row.provider_id,
                        stage="pdf_extraction",
                        error_type="PdfExtractionError",
                        message=doc_row.error or "unknown",
                        context={"url": doc.source_url},
                    )
                )

        for error in outcome.errors:
            session.add(ErrorRecord(provider_id=provider_row.provider_id, stage="crawl", error_type="CrawlError", message=error))
        for url in outcome.robots_disallowed:
            session.add(ErrorRecord(provider_id=provider_row.provider_id, stage="crawl", error_type="RobotsDisallowed", message=url))

        run.finished_at = datetime.utcnow()
        run.pages_crawled = len(outcome.pages)
        run.status = "COMPLETED"

        provider_row.crawl_status = "CRAWLED"
        provider_row.last_website_check = datetime.utcnow()
        _update_identity_fields(provider_row, outcome)


@app.command()
def crawl(
    pending: bool = typer.Option(False, "--pending", help="Crawl every provider with crawl_status=PENDING."),
    domain: str = typer.Option(None, "--domain", help="Crawl (or recrawl) a single domain."),
    concurrency: int = typer.Option(None, "--concurrency"),
) -> None:
    """Crawl pending (or a single named) provider website(s)."""
    configure_logging()
    init_db()
    settings = get_settings()

    with session_scope() as session:
        if domain:
            provider_row = session.get(Provider, domain)
            if provider_row is None:
                typer.echo(f"No provider found for domain {domain}")
                raise typer.Exit(1)
            providers = [provider_row]
        elif pending:
            providers = list(session.execute(select(Provider).where(Provider.crawl_status == "PENDING")).scalars())
        else:
            typer.echo("Specify --pending or --domain")
            raise typer.Exit(1)

        if not providers:
            typer.echo("Nothing to crawl.")
            return

        typer.echo(f"Crawling {len(providers)} provider(s)...")
        _run_crawl(session, providers, concurrency or settings.sil_crawl_concurrency, settings)
        typer.echo("Crawl complete.")


@app.command()
def recrawl(domain: str = typer.Option(..., "--domain", help="Domain to recrawl.")) -> None:
    """Recrawl a single, already-known provider domain."""
    configure_logging()
    init_db()
    settings = get_settings()
    with session_scope() as session:
        provider_row = session.get(Provider, domain)
        if provider_row is None:
            typer.echo(f"No provider found for domain {domain}")
            raise typer.Exit(1)
        _run_crawl(session, [provider_row], 1, settings)
        typer.echo(f"Recrawled {domain}.")


def _classify_provider(session, provider_row: Provider) -> None:
    """Run both classifiers for one provider against its most recent crawl
    run only. Pages/documents are kept across crawl runs for history/diffing
    (see Page's docstring in database.py), so without this scoping a stale
    prior snapshot would keep contributing to the score forever.

    Shared by the `classify` command and the dashboard's "re-run
    classification" action so both go through identical logic.
    """
    latest_run_id = _latest_crawl_run_id(session, provider_row.provider_id)
    page_stmt = select(Page).where(Page.provider_id == provider_row.provider_id)
    doc_stmt = select(Document).where(Document.provider_id == provider_row.provider_id)
    if latest_run_id is not None:
        page_stmt = page_stmt.where(Page.crawl_run_id == latest_run_id)
        doc_stmt = doc_stmt.where(Document.crawl_run_id == latest_run_id)
    pages = list(session.execute(page_stmt).scalars())
    documents = list(session.execute(doc_stmt).scalars())

    sources: list[TextSource] = []
    for page in pages:
        page_type = page.page_type or classify_page_type(page.url, page.page_title)
        if page.text and page.text.visible_text:
            sources.append(
                TextSource(text=page.text.visible_text, source_url=page.url, origin="VISIBLE_TEXT", page_title=page.page_title, page_type=page_type)
            )
        if page.meta_description:
            sources.append(
                TextSource(text=page.meta_description, source_url=page.url, origin="METADATA", page_title=page.page_title, page_type=page_type)
            )
        if page.text and page.text.footer_text:
            sources.append(
                TextSource(text=page.text.footer_text, source_url=page.url, origin="VISIBLE_TEXT", page_title=page.page_title, page_type=page_type)
            )
        if page.text and page.text.structured_data:
            sources.append(
                TextSource(
                    text=json.dumps(page.text.structured_data),
                    source_url=page.url,
                    origin="STRUCTURED_DATA",
                    page_title=page.page_title,
                    page_type=page_type,
                )
            )
    for doc in documents:
        if doc.extraction_status == "OK" and doc.extracted_text:
            sources.append(TextSource(text=doc.extracted_text, source_url=doc.source_url, origin="PDF_TEXT", page_type="general"))

    sil_result = classify_sil(sources, provider_domain=provider_row.domain)
    registration_result = classify_registration(sources, provider_domain=provider_row.domain)

    provider_row.sil_score = sil_result.score
    provider_row.sil_classification = sil_result.classification
    provider_row.sil_confidence = sil_result.confidence
    provider_row.registration_claim_status = registration_result.status
    provider_row.registration_claim_confidence = registration_result.confidence

    existing_sil_hashes = {
        row[0]
        for row in session.execute(select(SilEvidence.evidence_hash).where(SilEvidence.provider_id == provider_row.provider_id)).all()
    }
    for item in sil_result.evidence:
        h = evidence_hash(provider_row.domain, item.evidence_type, item.matched_text, item.source_url)
        if h in existing_sil_hashes:
            continue
        existing_sil_hashes.add(h)
        session.add(
            SilEvidence(
                provider_id=provider_row.provider_id,
                evidence_category=item.evidence_type,
                matched_text=item.matched_text,
                context_excerpt=item.context_excerpt,
                score_contribution=item.score_contribution,
                origin=item.origin,
                source_url=item.source_url,
                page_title=item.page_title,
                document_page=item.document_page,
                evidence_hash=h,
            )
        )

    existing_reg_hashes = {
        row[0]
        for row in session.execute(
            select(RegistrationEvidence.evidence_hash).where(RegistrationEvidence.provider_id == provider_row.provider_id)
        ).all()
    }
    for item in registration_result.evidence:
        h = evidence_hash(provider_row.domain, item.evidence_type, item.matched_text, item.source_url)
        if h in existing_reg_hashes:
            continue
        existing_reg_hashes.add(h)
        session.add(
            RegistrationEvidence(
                provider_id=provider_row.provider_id,
                claim_status=item.evidence_type,
                matched_text=item.matched_text,
                context_excerpt=item.context_excerpt,
                origin=item.origin,
                source_url=item.source_url,
                page_title=item.page_title,
                document_page=item.document_page,
                evidence_hash=h,
            )
        )

    record = _to_provider_record(provider_row, sil_result.evidence, registration_result.evidence)
    provider_row.automated_segment = compute_automated_segment(record)
    provider_row.crawl_status = "CLASSIFIED"


@app.command()
def classify(pending: bool = typer.Option(False, "--pending", help="Only classify providers not yet classified.")) -> None:
    """Run the SIL and registration-language classifiers over crawled content."""
    configure_logging()
    init_db()

    with session_scope() as session:
        stmt = select(Provider).where(Provider.crawl_status == "CRAWLED") if pending else select(Provider)
        providers = list(session.execute(stmt).scalars())
        typer.echo(f"Classifying {len(providers)} provider(s)...")

        for provider_row in providers:
            _classify_provider(session, provider_row)

        typer.echo("Classification complete.")


@app.command(name="export")
def export_cmd(format: str = typer.Option("csv", "--format", help="csv or json")) -> None:
    """Export the current dataset."""
    configure_logging()
    init_db()
    settings = get_settings()
    export_dir = resolve_data_path(settings.sil_export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    with session_scope() as session:
        providers = list(session.execute(select(Provider)).scalars())
        records = []
        for provider_row in providers:
            sil_rows = list(session.execute(select(SilEvidence).where(SilEvidence.provider_id == provider_row.provider_id)).scalars())
            reg_rows = list(
                session.execute(select(RegistrationEvidence).where(RegistrationEvidence.provider_id == provider_row.provider_id)).scalars()
            )
            sil_items = [
                EvidenceItem(
                    evidence_type=r.evidence_category,
                    source_url=r.source_url,
                    page_title=r.page_title,
                    matched_text=r.matched_text,
                    context_excerpt=r.context_excerpt,
                    score_contribution=r.score_contribution,
                    extracted_at=r.extracted_at,
                    origin=r.origin,
                    document_page=r.document_page,
                )
                for r in sil_rows
            ]
            reg_items = [
                EvidenceItem(
                    evidence_type=r.claim_status,
                    source_url=r.source_url,
                    page_title=r.page_title,
                    matched_text=r.matched_text,
                    context_excerpt=r.context_excerpt,
                    score_contribution=None,
                    extracted_at=r.extracted_at,
                    origin=r.origin,
                    document_page=r.document_page,
                )
                for r in reg_rows
            ]
            records.append(_to_provider_record(provider_row, sil_items, reg_items))

        if format == "csv":
            provider_path = export_dir / f"providers_{timestamp}.csv"
            evidence_path = export_dir / f"evidence_{timestamp}.csv"
            row_count = export_providers_csv(records, provider_path)
            export_evidence_csv(records, evidence_path)
            session.add(ExportRun(format="csv", row_count=row_count, file_path=str(provider_path)))
            typer.echo(f"Exported {row_count} providers to {provider_path}")
            typer.echo(f"Evidence exported to {evidence_path}")
        elif format == "json":
            json_path = export_dir / f"providers_{timestamp}.json"
            row_count = export_providers_json(records, json_path)
            session.add(ExportRun(format="json", row_count=row_count, file_path=str(json_path)))
            typer.echo(f"Exported {row_count} providers to {json_path}")
        else:
            typer.echo(f"Unknown format: {format}")
            raise typer.Exit(1)


@app.command(name="review-summary")
def review_summary() -> None:
    """Print provider counts by automated segment, for deciding next steps."""
    configure_logging()
    init_db()
    with session_scope() as session:
        providers = list(session.execute(select(Provider)).scalars())
        counts: dict[str, int] = {}
        for provider_row in providers:
            counts[provider_row.automated_segment] = counts.get(provider_row.automated_segment, 0) + 1

        typer.echo(f"Total providers: {len(providers)}")
        for segment, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            typer.echo(f"  {segment}: {count}")


@app.command(name="import-register")
def import_register(
    file: str = typer.Option(..., "--file", help="Path to the manually downloaded register export (CSV or Excel)."),
    snapshot_date: str = typer.Option(..., "--snapshot-date", help="Date the file was downloaded, YYYY-MM-DD."),
) -> None:
    """Import a downloaded NDIS Provider Register export as a new versioned snapshot."""
    configure_logging()
    init_db()

    try:
        parsed_date = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    except ValueError:
        typer.echo("--snapshot-date must be in YYYY-MM-DD format, e.g. 2026-07-25")
        raise typer.Exit(1)

    try:
        with session_scope() as session:
            result = import_register_file(file, parsed_date, session)
    except RegisterImportError as exc:
        typer.echo(f"Register import failed: {exc}")
        with session_scope() as session:
            session.add(ErrorRecord(stage="register_import", error_type="RegisterImportError", message=str(exc), context={"file": file}))
        raise typer.Exit(1)

    typer.echo(f"Imported snapshot {result.snapshot_id}: {result.imported} entries from {result.row_count} rows ({result.skipped} skipped).")
    typer.echo(f"Column mapping used: {result.column_mapping}")
    for warning in result.warnings:
        typer.echo(f"  warning: {warning}")


@app.command(name="match-register")
def match_register(
    pending: bool = typer.Option(False, "--pending", help="Only match providers not yet checked against any snapshot."),
) -> None:
    """Match crawled providers against the most recently imported register snapshot."""
    configure_logging()
    init_db()

    with session_scope() as session:
        latest_snapshot = session.execute(
            select(ProviderRegisterSnapshot).order_by(ProviderRegisterSnapshot.snapshot_id.desc())
        ).scalars().first()
        if latest_snapshot is None:
            typer.echo("No register snapshot has been imported yet - run `import-register` first.")
            raise typer.Exit(1)

        entry_rows = list(session.execute(select(RegisterEntry).where(RegisterEntry.snapshot_id == latest_snapshot.snapshot_id)).scalars())
        entries = [
            RegisterEntryData(
                entry_id=e.entry_id,
                abn=e.abn,
                entity_name=e.entity_name,
                trading_name=e.trading_name,
                registration_status=e.registration_status,
                registration_groups=e.registration_groups,
                registration_expiry=e.registration_expiry,
                state=e.state,
            )
            for e in entry_rows
        ]

        stmt = select(Provider)
        if pending:
            stmt = stmt.where(Provider.register_match_status == "REGISTER_NOT_CHECKED")
        providers = list(session.execute(stmt).scalars())
        typer.echo(f"Matching {len(providers)} provider(s) against snapshot {latest_snapshot.snapshot_id} ({len(entries)} register entries)...")

        for provider_row in providers:
            result = match_provider(
                provider_abn=provider_row.abn,
                provider_abn_confirmed=bool(provider_row.abn_lookup_confirmed),
                provider_legal_name=provider_row.legal_name,
                provider_trading_name=provider_row.trading_name,
                provider_states=list(provider_row.states or []),
                entries=entries,
            )

            provider_row.register_match_status = result.status
            provider_row.register_match_confidence = result.confidence
            provider_row.last_register_check = datetime.utcnow()
            provider_row.register_snapshot_date = latest_snapshot.download_date
            provider_row.register_entity_name = result.matched_entry.entity_name if result.matched_entry else None
            provider_row.register_abn = result.matched_entry.abn if result.matched_entry else None
            provider_row.register_registration_status = result.matched_entry.registration_status if result.matched_entry else None

            session.add(
                ProviderRegisterMatch(
                    provider_id=provider_row.provider_id,
                    snapshot_id=latest_snapshot.snapshot_id,
                    method=result.method,
                    confidence=result.confidence,
                    register_entity_name=result.matched_entry.entity_name if result.matched_entry else None,
                    register_abn=result.matched_entry.abn if result.matched_entry else None,
                    registration_status=result.matched_entry.registration_status if result.matched_entry else None,
                    registration_groups=result.matched_entry.registration_groups if result.matched_entry else [],
                    registration_expiry=result.matched_entry.registration_expiry if result.matched_entry else None,
                )
            )

            record = _to_provider_record(provider_row)
            provider_row.automated_segment = compute_automated_segment(record)

        typer.echo("Register matching complete.")


@app.command(name="abn-verify")
def abn_verify(
    pending: bool = typer.Option(False, "--pending", help="Only verify providers whose ABN hasn't been checked yet."),
) -> None:
    """Confirm extracted ABNs and resolve legal names via the ABN Lookup service."""
    configure_logging()
    init_db()
    settings = get_settings()

    if not settings.abn_lookup_guid:
        typer.echo("ABN_LOOKUP_GUID is not set - register at https://abr.business.gov.au/Tools/WebServices and set it in .env.")
        raise typer.Exit(1)

    client = CachingAbnLookupClient(AbrJsonAbnLookupClient(guid=settings.abn_lookup_guid))

    with session_scope() as session:
        stmt = select(Provider).where(Provider.abn.is_not(None))
        if pending:
            stmt = stmt.where(Provider.abn_lookup_confirmed.is_(None))
        providers = list(session.execute(stmt).scalars())
        typer.echo(f"Verifying {len(providers)} provider(s) against ABN Lookup...")

        for provider_row in providers:
            try:
                result = client.lookup_abn(provider_row.abn)
            except AbnLookupError as exc:
                session.add(
                    ErrorRecord(provider_id=provider_row.provider_id, stage="abn_verify", error_type="AbnLookupError", message=str(exc))
                )
                continue

            provider_row.abn_lookup_confirmed = result.found
            if result.found and result.entity_name and not result.is_suppressed:
                provider_row.legal_name = result.entity_name
                provider_row.normalised_legal_name = normalise_name(result.entity_name)

        typer.echo("ABN verification complete.")


if __name__ == "__main__":
    app()
