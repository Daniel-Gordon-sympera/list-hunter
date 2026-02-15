# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

Implementation in progress. All five pipeline phases are functional. The implementation pivoted from raw `httpx` to **Crawl4AI** (Playwright-based browser automation) to handle Cloudflare protection on superlawyers.com. The approved design is at `docs/plans/2026-02-10-superlawyers-scraper-design.md` — note that it still references `httpx`; the code is the source of truth for HTTP layer details. The original specification is `superlawyers_scraping_plan.md` (v2.0).

## What This Project Does

A Python async web scraper for the Super Lawyers attorney directory. Accepts a city/state location and produces a CSV with 33 structured columns per attorney, scraped from `attorneys.superlawyers.com` (listings) and `profiles.superlawyers.com` (individual profiles).

## Tech Stack

- Python 3.11+
- `crawl4ai` (Playwright-based browser automation for HTTP — bypasses Cloudflare)
- `beautifulsoup4` + `lxml` (HTML parsing)
- `tenacity` (retries with exponential backoff), `python-slugify` (URL slugs)
- `rich` (progress bars for crawl-listings and fetch-profiles)
- Testing: `pytest` with saved HTML fixtures for offline parser tests

## Commands

```bash
# Full pipeline (each phase is a separate command)
python cli.py discover "Los Angeles, CA"
python cli.py crawl-listings data/los-angeles_ca/practice_areas.json
python cli.py fetch-profiles data/los-angeles_ca/listings.json
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json

# Parallel crawl with 5 workers, limited to specific PAs
python cli.py crawl-listings --workers 5 --practice-areas "family-law,tax-law" data/los-angeles_ca/practice_areas.json

# Cap results at 100 unique attorneys
python cli.py crawl-listings --max-results 100 data/los-angeles_ca/practice_areas.json

# Re-crawl listings from scratch (ignore checkpoint)
python cli.py crawl-listings --force data/los-angeles_ca/practice_areas.json

# Re-parse without re-fetching (after fixing selectors)
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json

# Tests
pytest tests/
```

## Architecture: Separate Phase Commands

Phases communicate through files in `data/{city}_{state}/`, not in-memory state.

```
discover         → practice_areas.json    (state/city slugs + practice area list)
crawl-listings   → listings_{pa}.json     (per-PA checkpoint, deleted after merge)
                 → listings.json          (final merged output, UUID-deduped)
fetch-profiles   → html/{uuid}.html       (raw HTML, one file per attorney — idempotent)
                 → fetch_status.json      (uuid → success/failed/skipped)
parse-profiles   → records.json           (full 33-field AttorneyRecords, merged with listing pre-fill)
export           → output/*.csv           (cleaned, UTF-8 BOM, QUOTE_ALL)
                 → logs/{command}_{ts}.log (DEBUG-level file logs per command run)
```

`crawl-listings` runs practice areas in parallel (`--workers`, default 3). Each worker writes a per-PA file (`listings_{pa_slug}.json`) on completion. A final merge phase combines them into `listings.json` with UUID deduplication, then cleans up per-PA files. Interrupted runs resume automatically — completed per-PA files are detected and skipped. Use `--force` to re-crawl from scratch.

`fetch-profiles` is naturally idempotent — skips UUIDs with existing HTML files on disk.

## Project Structure

```
cli.py                      — CLI entry point (argparse, 5 subcommands)
main.py                     — Full pipeline runner (chains all 5 phases for a single location)
config.py                   — Constants (URLs, delays, concurrency limits)
models.py                   — AttorneyRecord dataclass (33 fields, 7 groups)
http_client.py              — Crawl4AI wrapper (stealth browser, semaphore, retry, CF detection)
log_setup.py                — Centralized logging (console + file handlers)
progress.py                 — Rich progress bars (CrawlProgress, FetchProgress)
spike.py                    — Validation spike (throwaway — fixture generation)
commands/
  discover.py               — Phase 1+2: resolve location, list practice areas
  crawl_listings.py         — Phase 3: parallel PA crawling, per-PA files, merge, checkpoint/resume
  fetch_profiles.py         — Phase 4a: download raw HTML to disk
  parse_profiles.py         — Phase 4b: parse saved HTML into records
  export.py                 — Phase 5: clean + write CSV
parsers/
  listing_parser.py         — Listing card HTML → partial AttorneyRecord
  profile_parser.py         — Profile page HTML → full AttorneyRecord
  address_parser.py         — Address block → street/city/state/zip
docs/                       — Research and plan documentation
resources/                  — Reference docs (crawl4ai, LLM API docs)
tests/
  fixtures/                 — Real HTML saved from scraping runs
  test_cli.py
  test_crawl_listings.py
  test_http_client.py
  test_log_setup.py
  test_progress.py
```

## Key Design Decisions

**Phases as separate commands**: Each phase reads/writes files. Allows re-running individual phases (especially parse-profiles) without re-fetching.

**Raw HTML saved to disk**: `fetch-profiles` saves `{uuid}.html` files. `parse-profiles` reads them. Fix a selector → re-parse without hitting the site.

**Two-phase data collection**: Phase 3 pre-fills 7 fields from listing cards. If a profile page fails, partial data is retained as fallback.

**Deduplication by UUID**: Attorneys appear across multiple practice area listings. UUID from profile URLs (`/([\w-]{36})\.html`) is the dedup key.

**Parallel PA crawling**: Practice areas are crawled concurrently via `--workers N` (default 3). Each worker writes an independent `listings_{pa_slug}.json` on completion, then a merge phase combines them with UUID dedup. This gives three layers of concurrency: PA workers (semaphore), per-request concurrency (ScraperClient semaphore), and inter-request delays (asyncio.sleep). Per-PA files act as natural checkpoints for resume.

**Profile tiers** (basic/expanded/premium): Inferred from data completeness. The scraper handles empty fields gracefully.

**Data cleaning in export, not parsers**: Parsers extract raw values. Export normalizes (phone formatting, URL param stripping, bio detection). Keeps parser output inspectable for debugging.

**Merge strategy**: Profile data wins over listing data. Listing data fills gaps.

**Multi-value field encoding**: `" ; "` (space-semicolon-space) delimiter. Internal semicolons replaced with commas.

**File logging**: Every command writes a DEBUG-level log to `data/{city}_{st}/logs/{command}_{timestamp}.log`. Console shows INFO by default (`-v` for DEBUG). Provides full post-mortem trace without cluttering the terminal.

**Progress bars**: Rich progress bars (CrawlProgress, FetchProgress) use a callback pattern so the progress display is decoupled from crawl logic. Disabled via `SUPERLAWYERS_NO_PROGRESS=1` for CI or piped output. Output goes to stderr to avoid interfering with stdout.

## Rate Limiting & Anti-Detection

- Three-layer concurrency model:
  - `--workers N` PA workers (default `DEFAULT_PA_WORKERS = 3`) — controls how many practice areas are crawled simultaneously
  - `ScraperClient` semaphore (`MAX_CONCURRENT = 3`) — limits concurrent HTTP requests across all workers
  - `asyncio.sleep(random.uniform(2.0, 5.0))` between requests — sleep runs *before* semaphore acquire for better throughput
- Exponential backoff via `tenacity` (2s → 4s → 8s, max 3 attempts)
- Crawl4AI stealth mode: real Chromium browser, `enable_stealth=True`, `override_navigator=True`, `--disable-blink-features=AutomationControlled`
- `Sec-Fetch-*` and `Accept-Language` headers sent via `BrowserConfig.headers`
- Persistent browser profile retains Cloudflare clearance cookies across runs
- Cloudflare challenge detection (7 HTML markers) + response header check (`cf-mitigated`) with automatic retry
- `--retry-cf` flag to selectively re-download challenge pages
- Proxy support via `PROXY_URL` env var (e.g. Bright Data residential proxy); rotates IPs server-side
- No UA rotation, no CAPTCHA solving in v1

## URL Patterns

- Listings: `attorneys.superlawyers.com/{practice-area}/{state}/{city}/?page=N`
- Profiles: `profiles.superlawyers.com/{state}/{city}/lawyer/{name-slug}/{uuid}.html`
- City index: `attorneys.superlawyers.com/{state}/{city}/` (source for practice area discovery)
- Only process `/lawyer/` URLs; skip `/lawfirm/` URLs
