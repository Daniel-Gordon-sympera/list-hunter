# Design: fetch-profiles Performance Optimization

**Date:** 2026-02-16
**Status:** Approved
**Goal:** Reduce wall-clock time for `fetch-profiles` from ~2.5 hours (5000 profiles) to ~3-5 minutes.

## Problem

The `fetch-profiles` command uses a single Playwright browser with 3 concurrent tabs and 2-5s inter-request delays. Every request also waits 2s for JS after page load (`delay_before_return_html`). For 5000 profiles with a residential proxy, this takes ~2.5 hours. The user has no way to tune concurrency, delays, or the JS wait.

## Design

Three complementary optimizations, composing together:

### 1. httpx Fast Path (default on)

Try each profile URL with raw `httpx` first. If the response is clean HTML (no Cloudflare challenge), save it and move on. Only CF-blocked profiles fall back to the browser.

**Phase 1 — httpx sweep:**
- Fetch all profiles with `httpx.AsyncClient` using the configured proxy and stealth headers
- 30 concurrent requests (httpx is lightweight)
- Apply inter-request delay (configurable via `--delay`)
- Detect CF challenges with existing `is_cloudflare_challenge_response()`
- Save clean HTML to disk immediately; collect CF-blocked UUIDs

**Phase 2 — browser mop-up:**
- Pass CF-blocked profiles to `ScraperPool` (see below)
- Full Playwright treatment for these only

**Disable with `--no-httpx` flag if needed.**

With a residential proxy, httpx should handle 80-95% of profiles. Expected time for 5000 profiles: ~2-3 minutes for Phase 1, plus ~1-2 minutes for the browser mop-up.

### 2. ScraperPool — Multiple Browser Instances

New class in `http_client.py` that manages N `ScraperClient` instances (each with its own Chromium browser process). Requests are distributed round-robin.

```
ScraperPool (N=5 browsers)
├── ScraperClient #0 (browser, semaphore=3 tabs)
├── ScraperClient #1 (browser, semaphore=3 tabs)
├── ...
└── ScraperClient #4 (browser, semaphore=3 tabs)
     → Total concurrency: 5 × 3 = 15 requests
```

- Same `.fetch()` API as `ScraperClient` — transparent to callers
- Each browser gets its own process, cookie jar, and rendering thread
- `num_browsers=1` gives identical behavior to current code (backward compat)
- Proxy rotates IPs server-side; each browser session appears as a different user
- 28GB RAM supports 8-10 browser instances comfortably (~300MB each)

### 3. Parameterize ScraperClient

Add constructor params to `ScraperClient` (all optional, defaulting to current `config.*` values):

| Param | Default | Purpose |
|-------|---------|---------|
| `max_concurrent` | `config.MAX_CONCURRENT` (3) | Tabs per browser (semaphore size) |
| `delay_min` | `config.DELAY_MIN` (2.0) | Min inter-request delay |
| `delay_max` | `config.DELAY_MAX` (5.0) | Max inter-request delay |
| `page_wait` | `config.DELAY_BEFORE_RETURN` (2.0) | Seconds to wait for JS after page load |

Use instance attributes in `fetch()` and `CrawlerRunConfig` instead of reading `config.*` directly. Backward-compatible — existing callers pass no args.

### 4. Batched Processing

Replace single `asyncio.gather()` with batched loop:

- Batch size: `max(100, num_browsers * 3 * 5)` (keeps all browsers saturated)
- Write intermediate `fetch_status.json` after each batch
- Extract `_write_status()` helper
- Reduces memory pressure for 5000+ profile runs
- Provides crash-recovery via intermediate status persistence

### 5. CLI Changes

New flags on `fetch-profiles` subparser:

```
--browsers N       Number of browser instances for fallback (default: 1)
--delay MIN,MAX    Inter-request delay range in seconds (default: 2.0,5.0)
--page-wait SECS   JS wait after page load in seconds (default: 2.0)
--no-httpx         Disable httpx fast path, use browser for all requests
--force            Re-download all HTML files
--retry-cf         Re-download only Cloudflare challenge pages
```

Usage examples:
```bash
# Default (httpx sweep + 1 browser fallback)
python cli.py fetch-profiles data/los-angeles_ca/listings.json

# Fast with proxy: httpx sweep + 5 browser fallback, short delays
python cli.py fetch-profiles --browsers 5 --delay 1.0,2.0 --page-wait 0.5 \
    data/los-angeles_ca/listings.json

# Aggressive: httpx sweep + 8 browsers, minimal delays
python cli.py fetch-profiles --browsers 8 --delay 0.5,1.5 --page-wait 0.5 \
    data/los-angeles_ca/listings.json

# Browser-only mode (no httpx)
python cli.py fetch-profiles --no-httpx --browsers 5 data/los-angeles_ca/listings.json
```

### 6. Progress Bar

Add `TimeRemainingColumn` to `FetchProgress` for ETA display.

## Files to Modify

| File | Changes |
|------|---------|
| `http_client.py` | Parameterize `ScraperClient.__init__`; add `ScraperPool` class |
| `commands/fetch_profiles.py` | Two-phase fetch (httpx sweep + browser mop-up); batched processing; `_write_status` helper; `_httpx_sweep()` function; new params on `run()` |
| `cli.py` | `--browsers`, `--delay`, `--page-wait`, `--no-httpx` flags; validation; passthrough |
| `config.py` | `DEFAULT_HTTPX_CONCURRENT = 30` constant |
| `main.py` | Thread browser/delay/page-wait params through `run_pipeline()` |
| `progress.py` | `TimeRemainingColumn` in `FetchProgress` |
| `requirements.txt` | Add `httpx` |
| `tests/test_http_client.py` | Tests for parameterized ScraperClient, ScraperPool |
| `tests/test_cli.py` | Tests for new flags, dispatch wiring |

## Expected Performance

| Config | Concurrent | 5000 profiles |
|--------|-----------|---------------|
| **Current** (1 browser, 3 tabs, 2-5s delay, 2s page-wait) | 3 | ~2.5 hours |
| httpx sweep only (30 concurrent, 1-2s delay) | 30 | ~5 min |
| httpx + 5 browsers fallback (1-2s delay, 0.5s page-wait) | 30 + 15 | ~3-4 min |
| httpx + 8 browsers fallback (0.5-1.5s delay, 0.5s page-wait) | 30 + 24 | **~2-3 min** |

## Implementation Order

1. `http_client.py` — parameterize `ScraperClient`, add `ScraperPool`
2. `requirements.txt` — add `httpx`
3. `commands/fetch_profiles.py` — two-phase fetch, batching, new params
4. `cli.py` — new flags, validation, passthrough
5. `config.py` — `DEFAULT_HTTPX_CONCURRENT`
6. `main.py` — thread params through pipeline
7. `progress.py` — ETA column
8. Tests throughout

## Verification

1. `pytest tests/ -v` — all existing tests pass (backward compatibility)
2. `python cli.py fetch-profiles --help` — shows all new flags
3. Default mode fetches profiles (httpx + 1 browser fallback)
4. `--browsers 5 --delay 1.0,2.0 --page-wait 0.5` — measurably faster
5. `--no-httpx --browsers 3` — browser-only mode works
6. Interrupt mid-run — `fetch_status.json` has partial results
7. Progress bar shows ETA
