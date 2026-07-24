# SIL Provider Research (Stage 3: register matching and identity verification)

A compliant lead-research tool that identifies Australian organisations with strong public
evidence of delivering Supported Independent Living (SIL) whose website does not clearly state
that they are a registered NDIS provider. It never concludes that an organisation is
"unregistered" - it flags candidates for manual verification against the official NDIS Provider
Register.

See the Stage 1 architecture notes (shared in the build conversation) for the full design
rationale, database schema, and open questions. This README covers what's implemented so far.

## What's implemented

**Stage 2 - discovery, crawling, classification:**

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

**Stage 3 - register matching and identity verification (this update):**

- A register-file importer (`import-register`) that accepts a manually downloaded CSV/Excel export
  of the official NDIS Provider Register, resolves its columns by configurable name aliases (see
  `config/register_columns.yml`), and stores each import as an additive, versioned snapshot. It
  fails loudly - listing the file's actual headers - if the structure doesn't match, rather than
  silently mis-mapping a column.
- An ABN Lookup client (`abn-verify`) behind a swappable interface, with a mock for tests, a live
  JSON-endpoint implementation, and database-backed response caching.
- A register-matching cascade (`match-register`): exact ABN match, exact normalised legal-name
  match, fuzzy legal-name match, fuzzy trading-name match corroborated by state overlap, and a
  weaker fuzzy fallback - in that priority order, with configurable thresholds. Any tier that
  finds more than one equally-good candidate returns `MULTIPLE_POSSIBLE_MATCHES` rather than
  guessing.
- Segmentation and the CSV/JSON export now reflect real register-match fields once matching has
  run.
- SQLite storage (portable to PostgreSQL via `SIL_DATABASE_URL`).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in any credentials you have
```

No credentials are required to run discovery/crawl/classify end-to-end: with
`SIL_SEARCH_PROVIDER=mock` (the default), discovery runs against an in-memory
`MockSearchProvider` instead of a live API. `import-register` and `match-register` don't need any
credentials either. `abn-verify` needs a free ABN Lookup GUID (see below).

## Environment variables

See `.env.example` for the full list with defaults. The ones you're most likely to set:

| Variable | Purpose |
|---|---|
| `SIL_DATABASE_URL` | SQLAlchemy DSN. Defaults to a local SQLite file. |
| `SIL_SEARCH_PROVIDER` | `mock` (default) or `google_cse`. |
| `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_CX` | Required only when `SIL_SEARCH_PROVIDER=google_cse`. |
| `SIL_DAILY_QUERY_BUDGET` | Caps queries per `discover` run (Google's free tier is 100/day). |
| `ABN_LOOKUP_GUID` | Required for `abn-verify`. Register for free at https://abr.business.gov.au/Tools/WebServices. |
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

# Import a manually downloaded NDIS Provider Register export as a new versioned snapshot.
python -m sil_research import-register --file path/to/register.csv --snapshot-date 2026-07-25

# Confirm extracted ABNs and resolve legal names via ABN Lookup (needs ABN_LOOKUP_GUID).
python -m sil_research abn-verify --pending

# Match crawled providers against the most recently imported register snapshot.
python -m sil_research match-register --pending

# Export the current dataset (providers + a one-row-per-evidence-item file).
python -m sil_research export --format csv
python -m sil_research export --format json

# Provider counts by automated segment - the input to the go/no-go decision on Stage 4/5.
python -m sil_research review-summary
```

Every command also works via the installed console script: `sil-research discover ...`.

## Downloading and importing a register snapshot

1. Download the current "Active providers" export from the NDIS Quality and Safeguards
   Commission's data portal (`dataresearch.ndis.gov.au/datasets/provider-datasets`) yourself - this
   tool does not, and will not, automate that download.
2. Check the file's header row against `config/register_columns.yml`. If a column name isn't
   recognised, add it to the relevant alias list rather than renaming the file - `import-register`
   will otherwise fail loudly and tell you exactly which required field it couldn't resolve and
   what headers it actually found.
3. Run `python -m sil_research import-register --file <path> --snapshot-date <YYYY-MM-DD>`, using
   the date you downloaded the file (not today's date, if different) as the snapshot date.
4. Each import is additive and versioned - re-running it with a newer file creates a new snapshot
   without touching the old one, and `match-register` always matches against the latest.

**The column aliases in `config/register_columns.yml` are best-effort guesses**, not confirmed
against a real downloaded file (the register's download page blocks automated fetching from this
environment). Treat the first real import as a validation step: if it fails, the error message
tells you what to add to the alias file.

## Testing

```bash
source .venv/bin/activate
python -m pytest
```

Tests run entirely offline: classifier/extraction tests use synthetic HTML fixtures under
`tests/fixtures/`, the crawler and ABN Lookup client tests mock HTTP with `respx`, and the register
importer/matcher tests use an in-memory SQLite database - nothing hits the network or a real file.

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
- The register importer's column-name aliases are unconfirmed against a real downloaded file (see
  above) - the first real import should be treated as a validation step.
- The ABN Lookup client's response field names (`Abn`, `AbnStatus`, `EntityName`, etc.) match the
  commonly published shape of the ABR's JSON endpoint, but couldn't be confirmed live from this
  environment either. The client retains the full raw response in its cache regardless, so a
  field-name mismatch is recoverable without re-querying once you can verify against a live GUID.
- Register matching's "trading name + corroboration" tier can only use state overlap as a
  corroborating signal today, since the register import doesn't expose domain/phone/address -
  revisit once the real file's columns are confirmed.
- Organisation-identity extraction (legal name, trading name, ABN) falls back to heuristic text
  matching wherever ABN Lookup hasn't confirmed a name - `abn_lookup_confirmed` on each record says
  which is which.
- The crawler's page-type detection (general/employment/blog) is a URL/title keyword heuristic; an
  unusually structured site could mis-tag a page and shift where SIL evidence gets attributed.
- Every automated output - SIL classification, registration-claim status, register-match status,
  and segment - is designed for manual review, not for any business or compliance decision on its
  own.

## Sample output record (CSV)

```
provider_id,provider_name,legal_name,abn,abn_valid,...,sil_score,sil_classification,...,registration_claim_status,...,register_match_status,register_registration_status,register_entity_name,register_abn,register_match_confidence,...,automated_segment,manual_review_status,...
sunriseliving.com.au,Sunrise Living,Sunrise Living Pty Ltd,53004085616,True,...,23,STRONG_SIL_EVIDENCE,...,EXPLICIT_REGISTERED_CLAIM,...,EXACT_ABN_MATCH,Active,Sunrise Living Pty Ltd,53004085616,0.97,...,UNSEGMENTED,PENDING,...
```

(That last example is a "clean" record - website claim and register both confirm registration, so
nothing is flagged. A record with `registration_claim_status=NO_REGISTERED_CLAIM_FOUND` and
`register_match_status=NO_CONFIDENT_MATCH` or `REGISTER_NOT_CHECKED` alongside strong SIL evidence
is the one segment A is built to surface.)

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
- `MULTIPLE_POSSIBLE_MATCHES` and `MANUAL_REVIEW_REQUIRED` register-match statuses are exactly
  that - the matcher found more than one plausible register entry, or a name match strong enough to
  be worth a look but not corroborated, and deliberately stopped short of picking one.
- A `NO_CONFIDENT_REGISTER_MATCH` (recorded internally as `NO_CONFIDENT_MATCH`) must never be read
  as "unregistered" - it means this tool couldn't confirm a match against the imported snapshot,
  not that no registration exists.

## Cohort status

No live discovery/crawl run has been executed yet - `SIL_SEARCH_PROVIDER` defaults to the offline
mock and no Google CSE credentials have been supplied in this environment, and no real NDIS
Provider Register file has been downloaded and imported. `review-summary` will report real segment
counts once discovery and matching have been run against live data; there is currently no cohort
to report, so the go/no-go decision on Stage 4/5 is still pending an actual run.
