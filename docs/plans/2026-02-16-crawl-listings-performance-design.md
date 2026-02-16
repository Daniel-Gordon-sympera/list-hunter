# Design: crawl-listings Performance Optimization

## Problem

`crawl-listings` is the pipeline bottleneck for large cities. For LA-sized cities (~30 practice areas, ~25 pages each), current wall-clock time ranges from 1.2 min (httpx best case) to 29 min (all browser fallback). Two root causes:

1. **Sequential pagination**: Each PA worker fetches page 1, parses, fetches page 2, parses, etc. No parallelism within a PA.
2. **Redundant page fetches**: Attorneys appear across multiple PAs. Deduplication only happens at merge time, after all pages are already fetched. For cities with heavy cross-PA overlap, 30-50% of page fetches yield only already-known attorneys.

## Solution

A three-phase pipeline with three dedup layers. Replaces the current independent-PA-worker model.

### Architecture

```
Phase 1: Scout         Fetch page 1 of ALL PAs via httpx (parallel)
                       Parse cards + pagination -> page count + initial UUIDs
                       Build global UUID set, calculate overlap per PA
                       Decision: crawl fully / skip PA

Phase 2: Bulk Fetch    For non-skipped PAs, fetch pages 2-N in parallel via httpx
                       Global dedup + threshold early-stop per PA
                       Collect CF-blocked URLs

Phase 3: Browser Mop   Re-fetch CF-blocked pages via ScraperPool
                       Same dedup layers applied
```

### Three Dedup Layers

| Layer | Trigger | Action |
|-------|---------|--------|
| PA-level skip | Page-1 overlap > 80% with global set | Skip entire PA |
| Page-level global dedup | 0 globally-new UUIDs on a page | Stop that PA's pagination |
| Threshold early stop | < 10% globally-new UUIDs on a page | Stop that PA's pagination |

## Phase 1: Scout

1. Generate page-1 URLs for all PAs (or `--practice-areas` filtered subset).
2. Fetch all page-1 URLs via httpx in parallel (semaphore-limited, `HTTPX_LISTING_CONCURRENT = 30`).
3. For each response:
   - Parse attorney cards via `parse_listing_page` (existing).
   - Parse pagination HTML via `parse_page_count` (new) to extract last page number.
   - CF-blocked responses: fetch via browser fallback.
4. Sort PAs by page count descending (broadest first).
5. Process page-1 results in sorted order, adding UUIDs to global set progressively.
6. Calculate overlap ratio per PA: `already_known / total_cards_on_page_1`.
7. Decision:
   - Overlap <= `PA_SKIP_OVERLAP_THRESHOLD` (0.80): mark for full crawl.
   - Overlap > threshold: skip (log, don't fetch remaining pages).

### Output

- `global_uuids: set` with all UUIDs from page-1 results.
- `pa_plans: list[PaPlan]` with per-PA: slug, page_count, skip/crawl, page-1 records.
- CF-blocked page-1 URLs for browser fallback.

### parse_page_count

New function in `parsers/listing_parser.py`. Extracts the last page number from pagination HTML:

```html
<ul class="pagination justify-content-center mb-0">
  <li class="page-item d-flex active"><span class="page-link">1</span></li>
  <li class="page-item d-flex"><a class="page-link" href="...?page=2" aria-label="Go to page 2">2</a></li>
  ...
  <li class="page_ellipsis" aria-label="Ellipses truncating pages 5-38">...</li>
  <li class="page-item d-flex"><a class="page-link" href="...?page=39" aria-label="Go to page 39">39</a></li>
  <li class="page-item d-flex fw-bold"><a rel="next" class="page-link" href="...?page=2">Next</a></li>
</ul>
```

Parses `aria-label="Go to page N"` attributes. Returns `max(N)` or `None` if no pagination found (single-page PA).

## Phase 2: Bulk Fetch

1. Generate all (PA, page) URL pairs for non-skipped PAs, pages 2 through page_count.
2. Fetch all URLs via httpx in parallel (`HTTPX_LISTING_CONCURRENT = 30`).
3. Process results in PA order, page order (required for dedup correctness):
   - Parse cards, check each UUID against `global_uuids`.
   - Count globally-new UUIDs on this page.
   - Threshold early stop: if `globally_new / total_cards < GLOBAL_DEDUP_THRESHOLD` (0.10), cancel remaining pages for this PA.
   - Page-level global dedup: if `globally_new == 0`, same effect (subset of threshold).
   - Add new UUIDs to `global_uuids`.
4. Discard results for cancelled pages.
5. Collect CF-blocked URLs for Phase 3.

### Ordering subtlety

All pages are fetched in parallel, but results are processed in page order per PA. The early-stop decision for page 5 depends on pages 1-4. Implementation: fire all fetches, collect results into a dict keyed by `(pa_slug, page_num)`, then iterate in order.

## Phase 3: Browser Mop-up

1. Combine CF-blocked URLs from Phase 1 and Phase 2 into a single queue.
2. Sort by PA breadth (descending) then page number (ascending) — builds global dedup set optimally.
3. Fetch via `ScraperPool` with `--browsers N`.
4. Apply same dedup layers as Phase 2.
5. Write per-PA files atomically (same as current).
6. Merge into final `listings.json`.

### Key improvement over current browser fallback

Currently each PA worker processes its own browser requests sequentially. The new design pools all CF-blocked pages into a single queue distributed across all browser instances. Better utilization — no browser sits idle while another PA's queue is full.

## Data Structures

### PaPlan

```python
@dataclass
class PaPlan:
    slug: str
    page_count: int | None       # from pagination parse, None = unknown
    page1_records: dict[str, dict]  # UUID -> record from page 1
    overlap_ratio: float          # fraction of page-1 UUIDs already in global set
    skip: bool                    # True if overlap > threshold
    pages_fetched: set[int]       # tracks which pages we have results for
    stopped_early: bool = False   # True if dedup threshold triggered
```

### Enhanced CrawlState

```python
@dataclass
class CrawlState:
    max_results: int | None = None
    stop_event: asyncio.Event
    global_uuids: set

    # New:
    pa_skip_threshold: float = 0.80
    global_dedup_threshold: float = 0.10
    skipped_pas: set[str]

    def check_page_dedup(self, new_uuids: set, total_cards: int) -> bool:
        """Return True if this page triggers early stop."""
        globally_new = new_uuids - self.global_uuids
        if total_cards == 0:
            return True
        return len(globally_new) / total_cards < self.global_dedup_threshold
```

## Config Constants

New constants in `config.py`:

```python
PA_SKIP_OVERLAP_THRESHOLD = 0.80    # skip PA if page-1 overlap exceeds this
GLOBAL_DEDUP_THRESHOLD = 0.10       # stop PA if <10% of cards are globally new
HTTPX_LISTING_CONCURRENT = 30       # httpx concurrency for listing pages
```

## Files Modified

| File | Changes |
|------|---------|
| `parsers/listing_parser.py` | Add `parse_page_count(html) -> int \| None` |
| `commands/crawl_listings.py` | Rewrite `run()` to three-phase pipeline; add `_scout_all_pas()`, `_build_pa_plans()`, `_bulk_fetch_pages()`, `_process_bulk_results()`, `_browser_mop_up()`; remove `_crawl_one_pa()`; enhance `CrawlState`; add `PaPlan` dataclass |
| `config.py` | Add `PA_SKIP_OVERLAP_THRESHOLD`, `GLOBAL_DEDUP_THRESHOLD`, `HTTPX_LISTING_CONCURRENT` |
| `progress.py` | Update `CrawlProgress` for three-phase display: phase indicator, skipped PA count, dedup savings |
| `cli.py` | No new flags; `--workers` repurposed or removed in favor of `HTTPX_LISTING_CONCURRENT` |
| `README.md` | Update crawl-listings docs: new behavior, performance, dedup explanation |
| `CLAUDE.md` | Update architecture, key design decisions, rate limiting sections |
| `tests/test_crawl_listings.py` | New tests for all three phases, dedup logic, PA skip, threshold early stop, parse_page_count; update existing tests for new `run()` signature |
| `tests/test_listing_parser.py` | Tests for `parse_page_count` |

## Checkpoint / Resume

- Per-PA files (`listings_{pa_slug}.json`) still written atomically on PA completion.
- Resume still detects completed per-PA files and skips those PAs.
- New: after Phase 1, write `crawl_scout.json` containing `pa_plans` + serialized `global_uuids`. If interrupted mid-Phase-2, resume skips Phase 1.
- `--force` clears all checkpoint files.

## Error Handling

- Phase 1 scout failure for a PA: browser fallback for page 1. If both fail, log error, skip PA.
- Phase 2 httpx failure (non-CF): queued for Phase 3 browser mop-up.
- Phase 3 browser failure: logged as failed (same as current).
- `parse_page_count` returns None: fall back to sequential pagination for that PA (safe degradation).
- All dedup thresholds are configurable constants.

## Expected Performance (LA-sized city, 30 PAs, ~25 pages avg)

| Scenario | Current | Optimized | Speedup |
|----------|---------|-----------|---------|
| Best (httpx works) | 1.2 min | 8s | ~10x |
| Mixed (30% CF) | 12.5 min | 1.1 min | ~11x |
| Worst (all browser) | 29 min | 5.7 min | ~5x |

Sources of improvement:
- 50% fewer pages fetched (three dedup layers).
- Parallel instead of sequential (remaining pages fetched all at once).

## Constraints

- Residential rotating proxy assumed (handles concurrency without IP bans).
- CF blocking status is mixed/variable — design handles both httpx-success and browser-fallback scenarios.
- Target city size: large (LA, NYC, Chicago). Works for smaller cities too (fewer PAs, faster in all cases).
