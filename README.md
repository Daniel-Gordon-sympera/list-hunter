# Super Lawyers Scraper

Scrapes attorney profiles from the Super Lawyers directory.
Accepts a city/state location and produces a CSV with 33 data fields per attorney.

## Quick Start

### Prerequisites
- Python 3.11+

### Install

```bash
# Windows PowerShell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install
```

### Scrape a city (full pipeline)

```bash
python cli.py discover "Los Angeles, CA"
python cli.py crawl-listings --workers 3 data/los-angeles_ca/practice_areas.json
python cli.py fetch-profiles data/los-angeles_ca/listings.json
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json
```

Output: `output/superlawyers_los-angeles_ca_YYYYMMDD_HHMMSS.csv`

### Quick sample (grab first 50 attorneys from two practice areas)

```bash
python cli.py discover "Austin, TX"
python cli.py crawl-listings --practice-areas "family-law,tax-law" --max-results 50 data/austin_tx/practice_areas.json
python cli.py fetch-profiles data/austin_tx/listings.json
python cli.py parse-profiles data/austin_tx/
python cli.py export data/austin_tx/records.json
```

## Commands

| Command | Input | Output | Notable flags |
|---------|-------|--------|---------------|
| discover | "City, ST" | practice_areas.json | |
| crawl-listings | practice_areas.json | listings.json | `--workers`, `--practice-areas`, `--max-results`, `--force` |
| fetch-profiles | listings.json | html/{uuid}.html files | `--force`, `--retry-cf`, `--browsers`, `--delay`, `--page-wait`, `--no-httpx` |
| parse-profiles | html/ dir + listings.json | records.json | |
| export | records.json | .csv file | `-o` output dir |

## Parallel Crawling

`crawl-listings` crawls practice areas in parallel using `--workers N` (default 3). Each worker paginates one practice area independently. On completion, per-PA results are merged into a single `listings.json` with UUID deduplication.

```bash
# Use 5 concurrent PA workers
python cli.py crawl-listings --workers 5 data/los-angeles_ca/practice_areas.json
```

## Filtering & Limiting

Restrict which practice areas to crawl, or cap the total number of attorneys:

```bash
# Only crawl specific practice areas
python cli.py crawl-listings --practice-areas "family-law,tax-law" data/los-angeles_ca/practice_areas.json

# Stop after collecting 100 unique attorneys
python cli.py crawl-listings --max-results 100 data/los-angeles_ca/practice_areas.json

# Combine both
python cli.py crawl-listings --practice-areas "family-law" --max-results 50 data/los-angeles_ca/practice_areas.json
```

## File Logging

Commands that operate on a data directory automatically write a DEBUG-level log file to `data/{city}_{st}/logs/`. The log filename includes the command name and timestamp:

```
data/los-angeles_ca/logs/crawl-listings_20260214_153012.log
```

Console output shows INFO-level messages by default. Use `-v` for DEBUG on the console too.

## Progress Bars

`crawl-listings` and `fetch-profiles` display Rich progress bars showing live status, completion counts, and elapsed time. Progress bars render to stderr so they don't interfere with piped output.

Disable progress bars (e.g. for CI or piped output):

```bash
SUPERLAWYERS_NO_PROGRESS=1 python cli.py crawl-listings data/los-angeles_ca/practice_areas.json
```

## Profile Fetching

`fetch-profiles` uses a two-phase approach: httpx first (fast, lightweight), then Crawl4AI browser fallback for failures. `ScraperPool` manages multiple browser instances with round-robin distribution.

```bash
# Tune performance: 3 browser instances, faster delays, shorter page wait
python cli.py fetch-profiles --browsers 3 --delay 1.0,3.0 --page-wait 1.5 data/los-angeles_ca/listings.json

# Skip httpx fast path, use browser for all profiles
python cli.py fetch-profiles --no-httpx data/los-angeles_ca/listings.json
```

## Full Pipeline Shortcut

Run all 5 phases in sequence for a single city:

```bash
python main.py "Los Angeles, CA"
```

## Re-parsing without re-fetching

If you need to fix a parser selector, you don't need to re-download anything:

```bash
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json
```

## Anti-Detection

- Stealth browser headers (`Sec-Fetch-*`, `Accept-Language`) sent with every request
- Cloudflare challenge detection (7 HTML markers + `cf-mitigated` response header) with automatic retry
- Proxy support via `PROXY_URL` env var (e.g. Bright Data residential proxy)
- Use `--retry-cf` on `fetch-profiles` to selectively re-download blocked pages

## Running tests

```bash
pytest tests/ -v
```
