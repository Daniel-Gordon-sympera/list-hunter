# Super Lawyers Scraper — v1 Design

Date: 2026-02-10
Status: Approved
Source: `superlawyers_scraping_plan.md` (v2.0), refined through brainstorming session

## Context

The original plan (`superlawyers_scraping_plan.md`) is a 1,690-line specification for a 5-phase attorney directory scraper. This design document captures the result of pressure-testing that plan and simplifying it for a buildable v1.

### What Changed From the Original Plan

| Original Plan | v1 Design |
|---|---|
| Monolithic 5-phase pipeline | Phases as separate CLI commands |
| Custom TokenBucket rate limiter | Simple `asyncio.sleep(random)` |
| Checkpoint/resume system | Not in v1 (phases are resumable by design) |
| Session rotation every 50 requests | Not in v1 (test if needed) |
| CSV + JSONL + SQLite export | CSV-only |
| Parse on the fly | Save raw HTML, parse separately |
| Build from the plan, trust selectors | Validation spike first |
| Fuzzy city matching (difflib) | Fail with clear error |
| ZIP code resolution (uszipcode) | Defer to v2 |

### What Stayed the Same

- All 33 columns in the data model (7 groups)
- `httpx` + `beautifulsoup4` + `lxml` stack
- Two-phase data collection (listing pre-fill + profile deep scrape)
- UUID-based deduplication
- Profile tier inference
- Auto-generated bio detection
- Polite rate limiting (1-3s delay, max 5 concurrent)

---

## Step 0: Validation Spike

Before writing any pipeline code, build a single throwaway script that makes 3 real HTTP requests and saves the HTML:

1. Fetch a **city index page** (`attorneys.superlawyers.com/california/los-angeles/`) — confirms practice area link structure
2. Fetch one **listing page** (`attorneys.superlawyers.com/business-and-corporate/california/los-angeles/`) — confirms card structure, profile URL format, pagination
3. Fetch one **profile page** (a real profile URL extracted from step 2) — confirms all 33-field selectors

Save each response as an HTML file. Manually inspect. Compare against the plan's assumed selectors. Document every discrepancy. These three files become the first test fixtures.

This spike should take 30 minutes and will either confirm the plan's selectors or reveal what needs fixing — before writing 500 lines of parsing code built on wrong assumptions.

---

## Project Structure

```
superlawyers/
├── cli.py                  — CLI entry point (argparse, dispatches to commands)
├── config.py               — Constants (URLs, delays, concurrency limits)
├── models.py               — AttorneyRecord dataclass (33 fields)
├── commands/
│   ├── discover.py         — Phase 1+2: resolve location, list practice areas
│   ├── crawl_listings.py   — Phase 3: paginate listings, save profile URLs + pre-fill data
│   ├── fetch_profiles.py   — Phase 4a: download raw HTML to disk
│   ├── parse_profiles.py   — Phase 4b: parse saved HTML into records
│   └── export.py           — Phase 5: clean + write CSV
├── parsers/
│   ├── listing_parser.py   — Listing card HTML → partial AttorneyRecord
│   ├── profile_parser.py   — Profile page HTML → full AttorneyRecord
│   └── address_parser.py   — Address block → street/city/state/zip
├── http_client.py          — Thin httpx wrapper (headers, delays, retries via tenacity)
├── tests/
│   ├── fixtures/           — Real HTML saved from spike + scraping runs
│   ├── test_listing_parser.py
│   ├── test_profile_parser.py
│   └── test_address_parser.py
├── requirements.txt
└── README.md
```

---

## Command Data Flow

Each command reads an input file and writes an output file to a `data/{city}_{state}/` working directory. No command depends on in-memory state from another — they communicate through files.

```
python cli.py discover "Los Angeles, CA"
  → writes: data/los-angeles_ca/practice_areas.json
    (list of slugs + resolved state/city slugs)

python cli.py crawl-listings data/los-angeles_ca/practice_areas.json
  → writes: data/los-angeles_ca/listings.json
    (dict of uuid → {partial AttorneyRecord fields, profile_url})
    Logs: "Crawled 130 practice areas, found 3,247 unique attorneys"

python cli.py fetch-profiles data/los-angeles_ca/listings.json
  → writes: data/los-angeles_ca/html/{uuid}.html  (one file per attorney)
  → writes: data/los-angeles_ca/fetch_status.json  (uuid → success/failed/skipped)
    Re-runnable: skips UUIDs that already have an HTML file on disk

python cli.py parse-profiles data/los-angeles_ca/
  → reads:  html/{uuid}.html files + listings.json (for pre-fill merge)
  → writes: data/los-angeles_ca/records.json  (list of full 33-field records)

python cli.py export data/los-angeles_ca/records.json
  → writes: output/superlawyers_los-angeles_ca_20260210.csv
```

The `fetch-profiles` command is naturally idempotent — if it crashes at attorney #800, rerun it and it picks up where it left off by checking which HTML files already exist on disk. This replaces the checkpoint/resume system from the original plan with zero additional code.

---

## HTTP Client & Rate Limiting

A thin wrapper around `httpx.AsyncClient` with three responsibilities:

**1. Realistic headers.** Rotate User-Agent from a pool of 10-15 real browser strings. Set Accept, Accept-Language, and Sec-Fetch headers to mimic Chrome. Set Referer to the logical parent page.

**2. Delay between requests.** `await asyncio.sleep(random.uniform(1.0, 3.0))` before each request. No token bucket, no rolling window. At 1-3 seconds per request with max 5 concurrent, this naturally stays within reasonable limits. If 429s appear, increase the delay range.

**3. Retries via tenacity.** `@retry` decorator with exponential backoff for 429/5xx responses. On 403, rotate User-Agent and retry once. On 404, skip. Three retries max, then log and move on.

No session rotation for v1. Add it later only if the site demonstrably tracks sessions.

Single file, roughly 60-80 lines. The original plan's `http_client.py` + `rate_limiter.py` + `user_agents.py` collapse into one module.

---

## Parsers & Error Handling

The three parsers are the heart of the project and the most likely to need iteration. Every extraction method is independently testable and independently fallible.

**`listing_parser.py`** — Takes raw listing page HTML, returns a list of partial `AttorneyRecord` objects (7 fields: uuid, name, firm_name, phone, description, selection_type, profile_url). One function: `parse_listing_page(html: str) -> list[AttorneyRecord]`.

**`profile_parser.py`** — Takes raw profile page HTML + URL, returns a full `AttorneyRecord`. One public method with ~20 private extraction methods (one per field or field group). Each wrapped in `_safe()` — try/except returning empty string on failure. One broken selector can't kill the record. Merge with listing pre-fill: profile values win, listing values fill gaps.

**`address_parser.py`** — Isolated because address parsing is fiddly. The `City, ST ZIP` regex on the last line is the starting point; real data will have edge cases. Keeping this separate makes it easy to add cases.

**Testing strategy**: Each parser gets a test file that loads real HTML fixtures from disk and asserts on extracted field values. When a selector breaks: save the failing HTML as a new fixture, write a failing test, fix the selector, confirm all fixtures still pass. The validation spike produces the initial fixture set.

No mock HTTP in parser tests — parsers are pure functions that take HTML strings.

---

## Data Model & Export

The `AttorneyRecord` dataclass stays as specified in the original plan — all 33 fields with empty string defaults. Helper methods: `csv_headers()`, `to_csv_row()`, `to_dict()`, `completeness_score()`, `infer_profile_tier()`, `_is_auto_bio()`.

**Data cleaning happens in the `export` command, not in the parsers.** Parsers extract raw values; export normalizes them. This separation matters for debugging — you see exactly what the parser extracted before cleaning.

Cleaning rules:
- Phone: strip `+1`, format as `XXX-XXX-XXXX`
- URLs: strip tracking params (`?adSubId`, `?fli=`)
- Multi-value fields: normalize to `" ; "` delimiter
- Auto-generated bios: detect via regex patterns, replace with empty string
- State: uppercase 2-letter abbreviation
- Truncate any cell over 10,000 chars

CSV output: `utf-8-sig` encoding (BOM for Excel), `QUOTE_ALL` quoting, file naming: `superlawyers_{city}_{state}_{YYYYMMDD_HHMMSS}.csv`.

JSONL and SQLite deferred. The `records.json` intermediate file already has all the data if needed later.

---

## Implementation Order

1. **Validation spike** — 3 HTTP requests, save HTML, verify selectors
2. **`models.py`** + **`config.py`** — Data model and constants
3. **Parsers** + **tests** — `profile_parser.py`, `listing_parser.py`, `address_parser.py` with fixture-based tests (using spike HTML)
4. **`http_client.py`** — Thin httpx wrapper
5. **Commands** in pipeline order — `discover`, `crawl-listings`, `fetch-profiles`, `parse-profiles`, `export`
6. **`cli.py`** — Wire commands to argparse
7. **End-to-end test** — Run full pipeline against one city, validate CSV output
