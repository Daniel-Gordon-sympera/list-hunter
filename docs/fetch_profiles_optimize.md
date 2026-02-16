# Plan: Performance Optimization for `fetch-profiles`

## Context

The `fetch-profiles` command downloads raw profile HTML pages to disk — one per attorney. Currently, concurrency is hardcoded at 3 with fixed 2-5s delays, giving ~0.86 req/s throughput. For 1000 profiles that's ~19 minutes. The user has no way to tune this. The `crawl-listings` command already has `--workers`; `fetch-profiles` should have parity plus delay tuning.

## Changes

### 1. Parameterize `ScraperClient` (highest impact)

**File: `http_client.py`** — Add `max_concurrent`, `delay_min`, `delay_max` to `__init__`:

```python
def __init__(self, max_concurrent=None, delay_min=None, delay_max=None):
    self._semaphore = asyncio.Semaphore(max_concurrent or config.MAX_CONCURRENT)
    self._delay_min = delay_min if delay_min is not None else config.DELAY_MIN
    self._delay_max = delay_max if delay_max is not None else config.DELAY_MAX
```

Use `self._delay_min`/`self._delay_max` in `fetch()` instead of reading `config.DELAY_*` directly.

Backward-compatible — all existing callers pass no args and get current defaults.

### 2. Add `--workers` and `--delay` flags to `fetch-profiles`

**File: `cli.py`** — Add to fetch-profiles subparser:
- `--workers N` — concurrent profile fetches (default: 3)
- `--delay MIN,MAX` — delay range in seconds (default: 2.0,5.0)

Parse delay as comma-separated floats with validation (two values, min <= max, min >= 0.5).

Update `cmd_fetch_profiles` to pass `workers` and `delay` through.

**File: `commands/fetch_profiles.py`** — Add `workers` and `delay` params to `run()`. Pass to `ScraperClient`:

```python
async with ScraperClient(max_concurrent=workers, delay_min=d_min, delay_max=d_max) as client:
```

**File: `main.py`** — Thread `fetch_workers` through `run_pipeline()`.

### 3. Batched processing with intermediate status writes

**File: `commands/fetch_profiles.py`** — Replace single `asyncio.gather()` with a batched loop (batch size 100, internal constant):

- Process `to_fetch` in chunks of 100
- After each batch, write intermediate `fetch_status.json` to disk
- Extract `_write_status()` helper to avoid duplication
- Move `status_path` computation before the batch loop

This doesn't improve throughput (semaphore already limits concurrency), but reduces memory pressure for large runs and provides crash-recovery via intermediate status persistence.

### 4. Add ETA to progress bar

**File: `progress.py`** — Add `TimeRemainingColumn` to `FetchProgress` display.

### 5. Update `CLAUDE.md` and `README.md`

Document the new flags in the commands table and usage examples.

## Files to Modify

| File | What changes |
|------|-------------|
| `http_client.py` | `ScraperClient.__init__` gains `max_concurrent`, `delay_min`, `delay_max`; `fetch()` uses instance attrs |
| `commands/fetch_profiles.py` | `run()` gains `workers`, `delay`; batched loop; `_write_status` helper; intermediate status writes |
| `cli.py` | `--workers`, `--delay` on fetch-profiles subparser; `cmd_fetch_profiles` passes them through |
| `main.py` | `run_pipeline()` gains optional `fetch_workers` param |
| `progress.py` | `TimeRemainingColumn` added to `FetchProgress` |
| `tests/test_http_client.py` | Tests for custom semaphore/delay values and defaults |
| `tests/test_cli.py` | Tests for new flags, dispatch wiring, delay parsing/validation |

## Implementation Order

1. `http_client.py` — parameterize ScraperClient
2. `commands/fetch_profiles.py` — add params, batching, intermediate writes
3. `cli.py` — add flags, validation, passthrough
4. `main.py` — thread fetch_workers through pipeline
5. `progress.py` — add ETA column
6. Tests — update in parallel with each change

## Expected Impact

| Config | Throughput | 1000 profiles |
|--------|-----------|---------------|
| Current (3 workers, 2-5s delay) | ~0.86 req/s | ~19 min |
| 5 workers, 2-5s delay | ~1.43 req/s | ~12 min |
| 5 workers, 1-3s delay | ~2.5 req/s | ~7 min |
| 8 workers, 0.5-1.5s delay (with proxy) | ~8 req/s | ~2 min |

## Verification

1. Run existing tests: `pytest tests/ -v` — all must pass (backward compatibility)
2. Test new flags parse correctly: `python cli.py fetch-profiles --help` shows `--workers`, `--delay`
3. Verify default behavior unchanged: `python cli.py fetch-profiles data/.../listings.json` uses 3 workers, 2-5s delay
4. Verify custom concurrency: `python cli.py fetch-profiles --workers 5 data/.../listings.json`
5. Verify custom delay: `python cli.py fetch-profiles --delay 1.0,3.0 data/.../listings.json`
6. Verify batching + intermediate writes: interrupt a run mid-batch, check `fetch_status.json` contains partial results
7. Verify ETA shows in progress bar during a fetch run

## Usage After Implementation

```bash
# Default (unchanged behavior)
python cli.py fetch-profiles data/los-angeles_ca/listings.json

# 5 concurrent workers, shorter delays
python cli.py fetch-profiles --workers 5 --delay 1.0,3.0 data/los-angeles_ca/listings.json

# Maximum throughput with proxy
python cli.py fetch-profiles --workers 8 --delay 0.5,1.5 data/los-angeles_ca/listings.json
```
