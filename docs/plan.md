# Implementation Plan: 4 Features for Super Lawyers Scraper

## Build Order

```
Feature 3 (File Logging)     -- no dependencies, foundation for Feature 4
    |
Feature 2 (PA Filter + Max)  -- validates stop logic before parallelism
    |
Feature 1 (Parallel PAs)     -- most invasive, uses Feature 2's max-results logic
    |
Feature 4 (Rich Progress)    -- wraps around final architecture from Features 1+3
```

## Feature 3: File Logging

**Files**: `log_setup.py` (new), `cli.py` (modify), `main.py` (modify), `tests/test_log_setup.py` (new)

- Created `log_setup.py` with `setup_logging()` that supports console + file handlers
- Root logger at DEBUG, console handler at INFO (or DEBUG with `--verbose`), file handler always at DEBUG
- File logs written to `data/{city}_{st}/logs/{command}_{YYYYMMDD_HHMMSS}.log`
- Moved `setup_logging()` calls into each `cmd_*` handler with `data_dir` context
- `root.handlers.clear()` prevents handler accumulation on repeated calls

## Feature 2: PA Filter + Max Results

**Files**: `cli.py` (modify), `commands/crawl_listings.py` (modify), `main.py` (modify), `tests/test_crawl_listings.py` (modify), `tests/test_cli.py` (modify)

- Added `--practice-areas` (comma-separated PA slugs) and `--max-results` (int) to crawl-listings subparser
- PA filter applied immediately after loading discovery data; unknown slugs logged as warnings
- Max results applied at merge phase for exact count

## Feature 1: Parallel PA Crawling

**Files**: `config.py` (modify), `cli.py` (modify), `commands/crawl_listings.py` (rewrite), `main.py` (modify), `tests/test_crawl_listings.py` (rewrite)

- Added `DEFAULT_PA_WORKERS = 3` to config
- `CrawlState` dataclass with `stop_event` (asyncio.Event) for cooperative stopping
- Per-PA worker function `_crawl_one_pa()` writes `listings_{pa_slug}.json` on completion
- Three-layer concurrency: worker semaphore, HTTP semaphore (shared), per-request delay
- Merge phase: load all per-PA files, UUID dedup, max_results trim, write final `listings.json`
- Resume: existing `listings_{pa}.json` files are detected and skipped
- Force: deletes all per-PA files before re-crawling

## Feature 4: Rich Progress Bar

**Files**: `progress.py` (new), `commands/crawl_listings.py` (modify), `commands/fetch_profiles.py` (modify), `log_setup.py` (modify), `requirements.txt` (modify), `tests/test_progress.py` (new)

- `CrawlProgress`: PA completion bar + attorney counter + active worker status
- `FetchProgress`: Simple completed/total bar for profile downloads
- Callback pattern: `progress_callback` parameter decouples display from logic
- `SUPERLAWYERS_NO_PROGRESS=1` disables progress bars
- Console handler level raised to WARNING when progress is active (prevents corruption)
- `rich` added to requirements.txt

## Complete File Change Matrix

| File | F3 | F2 | F1 | F4 |
|------|:--:|:--:|:--:|:--:|
| `log_setup.py` | NEW | | | MOD |
| `progress.py` | | | | NEW |
| `config.py` | | | MOD | |
| `cli.py` | MOD | MOD | MOD | MOD |
| `commands/crawl_listings.py` | | MOD | REWRITE | MOD |
| `commands/fetch_profiles.py` | | | | MOD |
| `main.py` | MOD | MOD | MOD | |
| `requirements.txt` | | | | MOD |
| `tests/test_log_setup.py` | NEW | | | |
| `tests/test_crawl_listings.py` | | MOD | REWRITE | |
| `tests/test_cli.py` | MOD | MOD | MOD | |
| `tests/test_progress.py` | | | | NEW |
