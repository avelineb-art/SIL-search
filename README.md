# SIL Provider Research (Stage 4: review dashboard)

A compliant lead-research tool that identifies Australian organisations with strong public
evidence of delivering Supported Independent Living (SIL) whose website does not clearly state
that they are a registered NDIS provider. It never concludes that an organisation is
"unregistered" - it flags candidates for manual verification against the official NDIS Provider
Register.

See the Stage 1 architecture notes (shared in the build conversation) for the full design
rationale, database schema, and open questions. This README covers what's implemented so far.

## What's implemented

**Stage 2 - discovery, crawling, classification:**

- A modular `SearchProvider` interface with a `MockSearchProvider` (offline/testing),
  `BraveSearchProvider` (currently in use - see below), `SerpApiProvider`, and `GoogleCSEProvider`
  (Google Programmable Search) - the latter two remain fully supported, just not the active choice.
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
- SQLite storage (portable to PostgreSQL via `SIL_DATABASE_URL`). Crawl history is retained across
  recrawls (each fetch is its own row tagged by crawl run) so changes over time can be compared -
  classification always scores only the most recent crawl.

**Stage 4 - review dashboard (this update):**

- A Streamlit dashboard (`dashboard/app.py`) with a filterable provider table (state, SIL score,
  SIL classification, registration-claim status, register-match status, automated segment,
  keyword search) and a per-provider detail view split into the five sections the build spec
  calls for: SIL evidence, registration statements, official register match, automated
  interpretation, and the human-review decision.
- From the detail view: record a manual-review decision with reviewer name and notes, recrawl the
  provider, re-run classification without recrawling, one-click "mark false positive", and compare
  a page's visible text between any two crawl dates (unified diff).
- CSV export from the dashboard, either a quick download of the current filtered view or the full
  provider+evidence export (same code path as the CLI's `export` command).
- All querying/mutation logic lives in `dashboard/data.py`, which has no Streamlit dependency and
  is unit-tested; `dashboard/app.py` is thin rendering on top of it.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard]"
cp .env.example .env   # then fill in any credentials you have
```

(Drop `dashboard` from the extras if you only need the CLI/pipeline - it pulls in Streamlit and
pandas, which nothing outside `dashboard/` depends on.)

No credentials are required to run discovery/crawl/classify end-to-end: with
`SIL_SEARCH_PROVIDER=mock` (the default), discovery runs against an in-memory
`MockSearchProvider` instead of a live API. `import-register` and `match-register` don't need any
credentials either. `abn-verify` needs a free ABN Lookup GUID (see below).

## Environment variables

See `.env.example` for the full list with defaults. The ones you're most likely to set:

| Variable | Purpose |
|---|---|
| `SIL_DATABASE_URL` | SQLAlchemy DSN. Defaults to a local SQLite file. |
| `SIL_SEARCH_PROVIDER` | `mock` (default), `brave` (currently in use), `serpapi`, or `google_cse`. |
| `BRAVE_SEARCH_API_KEY` | Required when `SIL_SEARCH_PROVIDER=brave`. Register at https://brave.com/search/api/. |
| `BRAVE_COUNTRY`, `BRAVE_SEARCH_LANG` | Default to `AU` / `en` - bias results to Australia regardless of query text. |
| `SERPAPI_API_KEY`, `GOOGLE_CSE_API_KEY`/`GOOGLE_CSE_CX` | Alternative providers, still supported, not currently active. |
| `SIL_DAILY_QUERY_BUDGET` | Caps queries per `discover` run - check whichever provider's plan you're on for its actual limit (they vary: e.g. SerpApi's free tier is 100/**month**, Google CSE's is 100/day). |
| `SIL_DISCOVERY_DELAY_SECONDS` | Delay between consecutive queries within a `discover` run (default `1.5`). Without pacing, the first/highest-priority queries in a batch can trip the provider's burst rate limit and silently come back empty or erroring - see "Known limitations". |
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

# Provider counts by automated segment - the input to the go/no-go decision on Stage 5.
python -m sil_research review-summary
```

Every command also works via the installed console script: `sil-research discover ...`.

## Running the dashboard

```bash
pip install -e ".[dashboard]"   # if you skipped it during setup
streamlit run dashboard/app.py
```

Opens on `http://localhost:8501` by default. It reads/writes the same database as the CLI
(`SIL_DATABASE_URL`) - run discovery/crawl/classify/register-matching via the CLI first so there's
something to review. Note that SQLite doesn't handle sustained concurrent access from two separate
processes well in some environments; if you hit an "unable to open database file" error while
running a CLI command with the dashboard open at the same time, stop the dashboard, run the CLI
command, then restart it (this is a SQLite/filesystem limitation, not specific to this tool - it's
one of the reasons Stage 1 scoped Postgres as the production database).

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
importer/matcher/dashboard-data tests use an in-memory SQLite database - nothing hits the network
or a real file. The dashboard's rendering (`dashboard/app.py`) isn't unit-tested (Streamlit UI code
generally isn't) but was verified with a headless Playwright smoke test against real crawled data.

## Search provider: Brave Search API

`BraveSearchProvider` (`discovery/search_provider.py`) is the active live discovery source. It
authenticates via the `X-Subscription-Token` header (never as a query param, so the key never
lands in logs or URLs) and defaults to `country="AU"` to bias results to Australia, regardless of
query text. State/city targeting (NSW/VIC/QLD prioritised by default) still comes from the query
generator's location list, same as before - see `config/locations.yml`.

```bash
SIL_SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=<your key>
```

Note: Brave's API paginates by *page* via an `offset` parameter (0-9), not by raw result index -
`BraveSearchProvider` derives `offset` as `start // count`, which only lines up cleanly if every
call for a given query uses the same `limit` (true of everything in this codebase today, since
`discover` doesn't paginate within a single run yet).

`SerpApiProvider` and `GoogleCSEProvider` both remain in the codebase and fully supported
(`SIL_SEARCH_PROVIDER=serpapi` / `google_cse`) - Brave is just the currently-active choice, not a
hard deletion, since the whole point of the `SearchProvider` interface is to keep the discovery
source swappable.

## Adding another search provider

Implement `sil_research.discovery.base.SearchProvider` (one method: `search(query, start, limit)
-> list[SearchResult]`, raising `SearchQuotaExceededError` on a quota/rate-limit response), then
wire it into `_build_search_provider()` in `cli.py` behind a new `SIL_SEARCH_PROVIDER` value. No
other module needs to change - discovery, crawling, classification, and export all depend only on
the `SearchProvider` interface.

## Known limitations

- `BraveSearchProvider`'s response parsing (`web.results` with `title`/`url`/`description`, falling
  back to a top-level `results` key) matches Brave's documented response shape, but has only been
  verified against synthetic/mocked responses (`respx`) - this environment's egress proxy blocks
  `api.search.brave.com` outright (same policy-based block as every other arbitrary external
  domain, confirmed via the proxy's own status endpoint), and no `BRAVE_SEARCH_API_KEY` was
  available to test with from here. Verify the first few real results by hand before trusting it
  unattended. `SerpApiProvider` carries the same caveat (untested against a live response, same
  reason).
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
- The dashboard's recrawl/reclassify actions run synchronously in the Streamlit process (no
  background job queue) - recrawling a provider blocks the UI for as long as the crawl takes.
  Acceptable for an internal single-reviewer tool; revisit if Stage 5 adds scheduling.
- SQLite doesn't reliably support two processes (e.g. the dashboard and a CLI command) writing at
  the same moment in every environment - see "Running the dashboard" above.
- **Fixed**: `discover` used to fire every query in a batch back-to-back with no delay. On a real
  Brave Search run this silently degraded the highest-priority queries at the start of a batch
  (state name, capital city, first regional centre) - they'd hit Brave's burst rate limit and come
  back as an empty result or a caught `SearchProviderError` before any later, lower-priority query
  did, since query order follows the state → metro → regional-centre priority in
  `config/locations.yml`. `discover` now paces queries `SIL_DISCOVERY_DELAY_SECONDS` apart (default
  `1.5`s). If you hit this before the fix landed, clear the affected rows from `search_queries` so
  the freshness-window dedup doesn't skip them on the next run, e.g.:
  `DELETE FROM search_queries WHERE location IN ('New South Wales', 'Sydney', 'Newcastle');`

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
counts once discovery and matching have been run against live data.

The build spec originally conditioned the dashboard (Stage 4) on reviewing the Stage 3 cohort size
first. That review hasn't happened - there's still no real cohort - but Stage 4 was built anyway on
explicit approval to proceed. Stage 5 (PostgreSQL, scheduling, incremental recrawling, improved
entity resolution, deployment docs) remains gated on an actual go-ahead, same as before.
