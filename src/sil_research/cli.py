"""Command-line interface. See README.md for full usage examples.

This module is the composition root: it wires the otherwise-independent
discovery / crawling / classification / export modules together against
the database. Business logic (scoring, extraction, matching) intentionally
lives in those modules, not here, so it stays unit-testable without a CLI
or a database.
"""

from __future__ import annotations

import json
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
from sil_research.discovery.search_provider import GoogleCSEProvider, MockSearchProvider
from sil_research.export.csv_export import export_evidence_csv, export_providers_csv
from sil_research.export.json_export import export_providers_json
from sil_research.extraction.abn import find_abns
from sil_research.extraction.contact import extract_general_email, extract_phone_numbers
from sil_research.extraction.locations import extract_service_locations, extract_states
from sil_research.extraction.organisation import find_copyright_name, find_legal_name_candidates, normalise_name
from sil_research.logging_config import configure_logging
from sil_research.models import EvidenceItem, ProviderRecord
from sil_research.review.workflow import compute_automated_segment

app = typer.Typer(help="Compliant SIL provider lead-research CLI", add_completion=False)


def _build_search_provider(settings) -> SearchProvider:
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

        providers_created = 0
        for candidate in batch:
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

        for crawled_page in outcome.pages:
            page_title = crawled_page.parsed.title if crawled_page.parsed else None
            page_type = classify_page_type(crawled_page.url, page_title)
            page_row = Page(
                provider_id=provider_row.provider_id,
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
            pages = list(session.execute(select(Page).where(Page.provider_id == provider_row.provider_id)).scalars())
            documents = list(session.execute(select(Document).where(Document.provider_id == provider_row.provider_id)).scalars())

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


if __name__ == "__main__":
    app()
