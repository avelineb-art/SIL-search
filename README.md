# SIL Provider Research (Stage 2: minimum viable system)

A compliant lead-research tool that identifies Australian organisations with strong public
evidence of delivering Supported Independent Living (SIL) whose website does not clearly state
that they are a registered NDIS provider. It never concludes that an organisation is
"unregistered" - it flags candidates for manual verification against the official NDIS Provider
Register (register cross-checking lands in Stage 3).

See the Stage 1 architecture notes (shared in the build conversation) for the full design
rationale, database schema, and open questions. This README covers what's implemented so far.

## What's implemented (Stage 2)

- A modular `SearchProvider` interface with a `MockSearchProvider` (offline/testing) and a
  `GoogleCSEProvider` (Google Programmable Search).
- A query generator combining configurable phrase and location lists, with a daily query budget,
  a freshness window, and a location-priority order (NSW/VIC/QLD first by default).
- Canonical-domain deduplication and discovery-result exclusion (social media, PDF-only results,
  generic directories, job boards - job ads/directory listings are used only to extract an
  employer name/website pointer, never as classification evidence).
- A respectful, same-domain crawler: robots.txt-aware, identified user agent, per-domain delay,
  bounded concurrency across domains, timeouts/retries, canonical-URL dedup, a per-domain page
  cap, and PDF text extraction for provider-linked documents.
- An explainable, rule-based SIL classifier and NDIS-registration-language classifier, both fully
  driven by `config/classification_rules.yml` - every score and matched phrase is retained as
  evidence with its source URL and excerpt.
- Organisation-identity extraction: trading/legal name candidates, ABN (with checksum
  validation), phone, email, states, and service locations.
- Automated segmentation (segments A-E from the build spec).
- SQLite storage (portable to PostgreSQL via `SIL_DATABASE_URL`) and CSV/JSON export.

**Not yet implemented** (Stage 3, pending review of the Stage 2 cohort): the official NDIS
Provider Register importer and matcher, and ABN Lookup API integration. `register_match_status`
currently always reads `REGISTER_NOT_CHECKED`, which the segmentation logic treats as an
unconfirmed match - never as "unregistered".

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in any credentials you have
```

No credentials are required to run the system end-to-end: with `SIL_SEARCH_PROVIDER=mock` (the
default), discovery runs against an in-memory `MockSearchProvider` instead of a live API.

## Environment variables

See `.env.example` for the full list with defaults. The ones you're most likely to set:

| Variable | Purpose |
|---|---|
| `SIL_DATABASE_URL` | SQLAlchemy DSN. Defaults to a local SQLite file. |
| `SIL_SEARCH_PROVIDER` | `mock` (default) or `google_cse`. |
| `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_CX` | Required only when `SIL_SEARCH_PROVIDER=google_cse`. |
| `SIL_DAILY_QUERY_BUDGET` | Caps queries per `discover` run (Google's free tier is 100/day). |
| `ABN_LOOKUP_GUID` | Not used until Stage 3; register at https://abr.business.gov.au/Tools/WebServices when ready. |
| `SIL_CRAWLER_USER_AGENT`, `SIL_CRAWL_DELAY_SECONDS`, `SIL_CRAWL_MAX_PAGES_PER_DOMAIN`, `SIL_CRAWL_CONCURRENCY` | Crawl politeness/scale controls. |

## Commands

```bash
# Generate and run search queries, filter/dedupe results, create pending provider records.
python -m sil_research discover --state QLD --limit 100

# Crawl every provider with crawl_status=PENDING (respects robots.txt, page limits, delays).
python -m sil_research crawl --pending

# Recrawl one already-known domain.
python -m sil_research recrawl --domain example.com.au

# Run the SIL and registration-language classifiers over crawled content.
python -m sil_research classify --pending

# Export the current dataset (providers + a one-row-per-evidence-item file).
python -m sil_research export --format csv
python -m sil_research export --format json

# Provider counts by automated segment - the input to the go/no-go decision on Stage 4/5.
python -m sil_research review-summary
```

Every command also works via the installed console script: `sil-research discover ...`.

## Testing

```bash
source .venv/bin/activate
python -m pytest
```

Tests run entirely offline: classifier/extraction tests use synthetic HTML fixtures under
`tests/fixtures/`, and the crawler test mocks HTTP with `respx` - nothing hits the network.

## Adding another search provider

Implement `sil_research.discovery.base.SearchProvider` (one method: `search(query, start, limit)
-> list[SearchResult]`, raising `SearchQuotaExceededError` on a quota/rate-limit response), then
wire it into `_build_search_provider()` in `cli.py` behind a new `SIL_SEARCH_PROVIDER` value. No
other module needs to change - discovery, crawling, classification, and export all depend only on
the `SearchProvider` interface.

## Known limitations

- Job-board discovery (Seek/Indeed/EthicalJobs) has the extraction *logic* (pull an employer name
  and website out of ad text) but no live scraper wired up yet: each board's terms of service need
  confirming before automated fetching runs against it.
- The register cross-check and ABN Lookup confirmation are Stage 3 work; until then, every
  provider's `register_match_status` is `REGISTER_NOT_CHECKED`.
- Organisation-identity extraction (legal name, trading name, ABN) is heuristic text matching, not
  authoritative - Stage 3's ABN Lookup integration will be the source of truth once available.
- The crawler's page-type detection (general/employment/blog) is a URL/title keyword heuristic; an
  unusually structured site could mis-tag a page and shift where SIL evidence gets attributed.
- Every automated output - SIL classification, registration-claim status, and segment - is
  designed for manual review, not for any business or compliance decision on its own.

## Sample output record (CSV)

```
provider_id,provider_name,legal_name,abn,abn_valid,...,sil_score,sil_classification,...,registration_claim_status,...,automated_segment,manual_review_status,...
sunriseliving.com.au,Sunrise Living,Sunrise Living Pty Ltd,53004085616,True,...,23,STRONG_SIL_EVIDENCE,...,EXPLICIT_REGISTERED_CLAIM,...,WEBSITE_REGISTRATION_CLAIM_REQUIRES_VERIFICATION,PENDING,...
```

The accompanying evidence export has one row per matched phrase, e.g.:

```
provider_id,domain,evidence_kind,evidence_type,score_contribution,matched_text,context_excerpt,source_url,...
sunriseliving.com.au,sunriseliving.com.au,SIL,exact_sil_phrase,5,Supported Independent Living,"...offer Supported Independent Living (SIL) to NDIS participants...",https://sunriseliving.com.au/,...
```

## Areas requiring human verification

- Every record's `manual_review_status` starts at `PENDING` - nothing here is a finished
  conclusion about any organisation's registration status.
- SIL classification in the `POSSIBLE_SIL_PROVIDER` band (segment D) genuinely needs a human read:
  it may be SDA, support coordination, or an accommodation referral rather than direct SIL
  delivery.
- `EXPLICIT_UNREGISTERED_PROVIDER_CLAIM` (segment E) still needs verification - a website can be
  outdated or wrong about its own status.
- Any `CONFLICTING_REGISTRATION_INFORMATION` result means the site says contradictory things in
  different places and needs a reviewer to read both.
