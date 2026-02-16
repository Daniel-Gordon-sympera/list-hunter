# crawl-listings Performance Optimization — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace sequential per-PA pagination with a three-phase parallel pipeline and three dedup layers, achieving 5-11x speedup for large cities.

**Architecture:** Phase 1 (Scout) fetches page 1 of all PAs via httpx to discover page counts and build a global UUID set. Phase 2 (Bulk Fetch) fetches all remaining pages in parallel via httpx with dedup-based early stop. Phase 3 (Browser Mop-up) re-fetches CF-blocked pages through ScraperPool. Three dedup layers (PA-level skip, page-level global dedup, threshold early stop) eliminate ~50% of page fetches.

**Tech Stack:** Python 3.11+, httpx, crawl4ai, asyncio, beautifulsoup4, pytest

**Design doc:** `docs/plans/2026-02-16-crawl-listings-performance-design.md`

---

### Task 1: Add `parse_page_count` to listing parser

**Files:**
- Modify: `parsers/listing_parser.py:99` (add function at end)
- Test: `tests/test_listing_parser.py:251` (add tests at end)

**Step 1: Write failing tests for `parse_page_count`**

Add to `tests/test_listing_parser.py` after line 251:

```python
from parsers.listing_parser import parse_page_count


class TestParsePageCount:
    def test_extracts_last_page_from_pagination(self):
        html = '''<html><body>
        <ul class="pagination justify-content-center mb-0">
          <li class="page-item d-flex active"><span class="page-link">1</span></li>
          <li class="page-item d-flex"><a class="page-link" href="?page=2" aria-label="Go to page 2">2</a></li>
          <li class="page-item d-flex"><a class="page-link" href="?page=3" aria-label="Go to page 3">3</a></li>
          <li class="page_ellipsis" aria-label="Ellipses truncating pages 4-18">…</li>
          <li class="page-item d-flex"><a class="page-link" href="?page=19" aria-label="Go to page 19">19</a></li>
          <li class="page-item d-flex fw-bold">
            <a rel="next" class="page-link" href="?page=2"><span>Next</span></a>
          </li>
        </ul>
        </body></html>'''
        assert parse_page_count(html) == 19

    def test_returns_none_for_no_pagination(self):
        html = '<html><body><div>No pagination here</div></body></html>'
        assert parse_page_count(html) is None

    def test_returns_none_for_single_page(self):
        """Single active page with no other page links."""
        html = '''<html><body>
        <ul class="pagination">
          <li class="page-item active"><span class="page-link">1</span></li>
        </ul>
        </body></html>'''
        assert parse_page_count(html) is None

    def test_two_pages(self):
        html = '''<html><body>
        <ul class="pagination">
          <li class="page-item active"><span class="page-link">1</span></li>
          <li class="page-item"><a class="page-link" href="?page=2" aria-label="Go to page 2">2</a></li>
          <li class="page-item fw-bold"><a rel="next" class="page-link" href="?page=2">Next</a></li>
        </ul>
        </body></html>'''
        assert parse_page_count(html) == 2

    def test_real_fixture(self):
        """parse_page_count on the real listing_page.html fixture."""
        html = _load_fixture("listing_page.html")
        result = parse_page_count(html)
        assert result == 39

    def test_empty_html(self):
        assert parse_page_count("") is None
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_listing_parser.py::TestParsePageCount -v`
Expected: FAIL — `parse_page_count` does not exist.

**Step 3: Implement `parse_page_count`**

Add to `parsers/listing_parser.py` after line 99:

```python
_PAGE_LABEL_PATTERN = re.compile(r'aria-label="Go to page (\d+)"')


def parse_page_count(html: str) -> int | None:
    """Extract the last page number from pagination HTML.

    Looks for page links with aria-label="Go to page N" and returns
    the highest N found. Returns None if no pagination is present
    (single-page result or no results).
    """
    if not html:
        return None
    matches = _PAGE_LABEL_PATTERN.findall(html)
    if not matches:
        return None
    page_nums = [int(m) for m in matches]
    last_page = max(page_nums)
    return last_page if last_page > 1 else None
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_listing_parser.py -v`
Expected: ALL PASS (new + existing).

**Step 5: Commit**

```bash
git add parsers/listing_parser.py tests/test_listing_parser.py
git commit -m "feat: add parse_page_count to extract pagination from listing pages"
```

---

### Task 2: Add config constants for dedup thresholds

**Files:**
- Modify: `config.py:31` (add after `DEFAULT_PA_WORKERS`)

**Step 1: Add constants**

Add to `config.py` after line 31 (`DEFAULT_PA_WORKERS = 3`):

```python
# Dedup thresholds (crawl-listings optimization)
PA_SKIP_OVERLAP_THRESHOLD = 0.80   # skip PA if page-1 overlap exceeds this
GLOBAL_DEDUP_THRESHOLD = 0.10      # stop PA pagination if <10% of cards are globally new
HTTPX_LISTING_CONCURRENT = 30      # httpx concurrency for listing page fetches
```

**Step 2: Verify import works**

Run: `python -c "import config; print(config.PA_SKIP_OVERLAP_THRESHOLD, config.GLOBAL_DEDUP_THRESHOLD, config.HTTPX_LISTING_CONCURRENT)"`
Expected: `0.8 0.1 30`

**Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add dedup threshold and httpx concurrency constants"
```

---

### Task 3: Enhance CrawlState and add PaPlan dataclass

**Files:**
- Modify: `commands/crawl_listings.py:57-80` (replace CrawlState)
- Test: `tests/test_crawl_listings.py` (add new tests, update imports)

**Step 1: Write failing tests for enhanced CrawlState and PaPlan**

Add to `tests/test_crawl_listings.py` imports (line 10-18), updating to:

```python
from commands.crawl_listings import (
    CrawlState,
    PaPlan,
    _atomic_write,
    _cleanup_pa_files,
    _find_completed_pa_files,
    _httpx_fetch_listing_page,
    _merge_pa_files,
    run,
)
```

Add new test class after `TestCrawlState` (after line 153):

```python
class TestCrawlStateDedup:
    def test_check_page_dedup_below_threshold(self):
        """Pages with <10% new UUIDs trigger early stop."""
        state = CrawlState()
        state.global_uuids = {f"uuid-{i}" for i in range(18)}
        # Page has 20 cards, 18 already known, 2 new = 10%
        new_uuids = {f"uuid-{i}" for i in range(20)}
        # At exactly 10% (2/20), should NOT trigger (threshold is strict <)
        assert state.check_page_dedup(new_uuids, 20) is False

    def test_check_page_dedup_above_threshold(self):
        """Pages with >=10% new UUIDs do not trigger early stop."""
        state = CrawlState()
        state.global_uuids = {f"uuid-{i}" for i in range(15)}
        new_uuids = {f"uuid-{i}" for i in range(20)}
        # 5 new out of 20 = 25%
        assert state.check_page_dedup(new_uuids, 20) is False

    def test_check_page_dedup_zero_new(self):
        """Pages with 0 new UUIDs trigger early stop."""
        state = CrawlState()
        state.global_uuids = {f"uuid-{i}" for i in range(20)}
        new_uuids = {f"uuid-{i}" for i in range(20)}
        assert state.check_page_dedup(new_uuids, 20) is True

    def test_check_page_dedup_empty_page(self):
        """Empty pages (0 cards) trigger early stop."""
        state = CrawlState()
        assert state.check_page_dedup(set(), 0) is True

    def test_check_page_dedup_one_new_of_twenty(self):
        """1 new out of 20 = 5% < 10% threshold => triggers stop."""
        state = CrawlState()
        state.global_uuids = {f"uuid-{i}" for i in range(19)}
        new_uuids = {f"uuid-{i}" for i in range(20)}
        assert state.check_page_dedup(new_uuids, 20) is True

    def test_check_page_dedup_custom_threshold(self):
        """Custom threshold should be respected."""
        state = CrawlState(global_dedup_threshold=0.50)
        state.global_uuids = {f"uuid-{i}" for i in range(12)}
        new_uuids = {f"uuid-{i}" for i in range(20)}
        # 8 new out of 20 = 40% < 50% threshold => triggers stop
        assert state.check_page_dedup(new_uuids, 20) is True


class TestPaPlan:
    def test_creation_defaults(self):
        plan = PaPlan(slug="family-law", page_count=39)
        assert plan.slug == "family-law"
        assert plan.page_count == 39
        assert plan.page1_records == {}
        assert plan.overlap_ratio == 0.0
        assert plan.skip is False
        assert plan.stopped_early is False

    def test_skip_flag(self):
        plan = PaPlan(slug="divorce", page_count=10, overlap_ratio=0.90, skip=True)
        assert plan.skip is True
        assert plan.overlap_ratio == 0.90
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crawl_listings.py::TestCrawlStateDedup -v`
Expected: FAIL — `check_page_dedup` method doesn't exist.

Run: `pytest tests/test_crawl_listings.py::TestPaPlan -v`
Expected: FAIL — `PaPlan` doesn't exist.

**Step 3: Implement enhanced CrawlState and PaPlan**

In `commands/crawl_listings.py`, replace lines 57-80 (the entire `CrawlState` class) with:

```python
@dataclass
class PaPlan:
    """Plan for crawling a single practice area, built during scout phase."""

    slug: str
    page_count: int | None = None
    page1_records: dict[str, dict] = field(default_factory=dict)
    overlap_ratio: float = 0.0
    skip: bool = False
    stopped_early: bool = False


@dataclass
class CrawlState:
    """Shared state for the crawl pipeline.

    Asyncio-safe (single-threaded event loop — no locks needed).
    """

    max_results: int | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    global_uuids: set = field(default_factory=set)
    pa_skip_threshold: float = field(default_factory=lambda: config.PA_SKIP_OVERLAP_THRESHOLD)
    global_dedup_threshold: float = field(default_factory=lambda: config.GLOBAL_DEDUP_THRESHOLD)
    skipped_pas: set = field(default_factory=set)

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def add_uuids(self, uuids: set[str]) -> bool:
        """Register new UUIDs and check if max_results cap is reached.

        Returns True if the cap has been reached.
        """
        self.global_uuids.update(uuids)
        if self.max_results and len(self.global_uuids) >= self.max_results:
            self.stop_event.set()
            return True
        return False

    def check_page_dedup(self, page_uuids: set[str], total_cards: int) -> bool:
        """Return True if this page should trigger early stop for its PA.

        Triggers when the ratio of globally-new UUIDs to total cards
        falls below the dedup threshold.
        """
        if total_cards == 0:
            return True
        globally_new = page_uuids - self.global_uuids
        ratio = len(globally_new) / total_cards
        return ratio < self.global_dedup_threshold
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crawl_listings.py::TestCrawlState tests/test_crawl_listings.py::TestCrawlStateDedup tests/test_crawl_listings.py::TestPaPlan -v`
Expected: ALL PASS (existing CrawlState tests still pass + new tests pass).

**Step 5: Commit**

```bash
git add commands/crawl_listings.py tests/test_crawl_listings.py
git commit -m "feat: add PaPlan dataclass and dedup methods to CrawlState"
```

---

### Task 4: Implement Phase 1 — Scout

**Files:**
- Modify: `commands/crawl_listings.py` (add `_scout_all_pas` and `_build_pa_plans`)
- Test: `tests/test_crawl_listings.py`

**Step 1: Write failing tests for scout functions**

Add to `tests/test_crawl_listings.py` after the `TestPaPlan` class. Also add `parse_page_count` to the import from `parsers.listing_parser` if needed for mocking:

```python
class TestScoutPhase:
    @pytest.mark.asyncio
    async def test_scout_fetches_page1_for_all_pas(self):
        """_scout_all_pas should fetch page 1 for every PA."""
        fetched_urls = []

        async def fake_httpx_fetch(client, url, referer=None):
            fetched_urls.append(url)
            return "<html>page</html>", "success"

        mock_httpx_client = AsyncMock()

        cards = [_make_card("uuid-1")]
        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=cards), \
             patch("commands.crawl_listings.parse_page_count", return_value=5):
            from commands.crawl_listings import _scout_all_pas
            results = await _scout_all_pas(
                httpx_client=mock_httpx_client,
                browser_client=None,
                pa_slugs=["family-law", "tax-law"],
                state_slug="california",
                city_slug="los-angeles",
                referer="https://attorneys.superlawyers.com/california/los-angeles/",
                no_httpx=False,
            )

        assert len(results) == 2
        assert any("family-law" in u and "page=1" in u for u in fetched_urls)
        assert any("tax-law" in u and "page=1" in u for u in fetched_urls)

    @pytest.mark.asyncio
    async def test_scout_cf_blocked_falls_back_to_browser(self):
        """CF-blocked page-1 should fall back to browser."""
        browser_urls = []

        async def fake_httpx_fetch(client, url, referer=None):
            return None, "cf_blocked"

        async def fake_browser_fetch(url, referer=None):
            browser_urls.append(url)
            return "<html>browser</html>"

        mock_pool = _mock_scraper_pool(fake_browser_fetch)

        cards = [_make_card("uuid-1")]
        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=cards), \
             patch("commands.crawl_listings.parse_page_count", return_value=3):
            from commands.crawl_listings import _scout_all_pas
            results = await _scout_all_pas(
                httpx_client=AsyncMock(),
                browser_client=mock_pool,
                pa_slugs=["family-law"],
                state_slug="california",
                city_slug="los-angeles",
                referer="https://example.com/",
                no_httpx=False,
            )

        assert len(results) == 1
        assert any("family-law" in u for u in browser_urls)


class TestBuildPaPlans:
    def test_skips_high_overlap_pas(self):
        """PAs with >80% overlap on page 1 should be marked skip=True."""
        from commands.crawl_listings import _build_pa_plans

        global_uuids = {f"uuid-{i}" for i in range(18)}  # 18 known
        scout_results = [
            {
                "pa_slug": "family-law",
                "page_count": 30,
                "cards": [_make_card(f"uuid-{i}") for i in range(20)],  # 18 known + 2 new
            },
            {
                "pa_slug": "divorce",
                "page_count": 15,
                "cards": [_make_card(f"uuid-{i}") for i in range(20)],  # 18 known + 2 new
            },
        ]
        state = CrawlState()
        state.global_uuids = set()  # empty at start

        plans = _build_pa_plans(scout_results, state)

        # Sorted by page_count desc: family-law (30) first
        assert plans[0].slug == "family-law"
        assert plans[0].skip is False  # first PA, no overlap yet

        # divorce processed second: all 20 UUIDs already known from family-law
        assert plans[1].slug == "divorce"
        assert plans[1].skip is True
        assert plans[1].overlap_ratio == 1.0

    def test_preserves_page_count(self):
        from commands.crawl_listings import _build_pa_plans

        scout_results = [
            {
                "pa_slug": "tax-law",
                "page_count": 42,
                "cards": [_make_card("uuid-new")],
            },
        ]
        state = CrawlState()
        plans = _build_pa_plans(scout_results, state)
        assert plans[0].page_count == 42

    def test_single_pa_never_skipped(self):
        from commands.crawl_listings import _build_pa_plans

        scout_results = [
            {
                "pa_slug": "only-pa",
                "page_count": 5,
                "cards": [_make_card("uuid-1")],
            },
        ]
        state = CrawlState()
        plans = _build_pa_plans(scout_results, state)
        assert plans[0].skip is False
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crawl_listings.py::TestScoutPhase -v`
Expected: FAIL — `_scout_all_pas` doesn't exist.

Run: `pytest tests/test_crawl_listings.py::TestBuildPaPlans -v`
Expected: FAIL — `_build_pa_plans` doesn't exist.

**Step 3: Implement scout functions**

Add new import to `commands/crawl_listings.py` line 34:

```python
from parsers.listing_parser import parse_listing_page, parse_page_count
```

Add these functions after `_httpx_fetch_listing_page` (after line 115), before `_crawl_one_pa`:

```python
async def _scout_all_pas(
    httpx_client: httpx.AsyncClient | None,
    browser_client: ScraperPool | None,
    pa_slugs: list[str],
    state_slug: str,
    city_slug: str,
    referer: str,
    no_httpx: bool = False,
) -> list[dict]:
    """Phase 1: Fetch page 1 of every PA to discover page counts and initial UUIDs.

    Returns a list of dicts: [{"pa_slug", "page_count", "cards"}, ...]
    """
    sem = asyncio.Semaphore(config.HTTPX_LISTING_CONCURRENT)

    async def fetch_page1(pa_slug: str) -> dict:
        url = f"{config.BASE_URL}/{pa_slug}/{state_slug}/{city_slug}/?page=1"
        html = None

        async with sem:
            if not no_httpx and httpx_client is not None:
                html, status = await _httpx_fetch_listing_page(
                    httpx_client, url, referer=referer,
                )
                if status != "success":
                    html = None

            if html is None and browser_client is not None:
                html = await browser_client.fetch(url, referer=referer)

        if html is None:
            log.warning("Scout: failed to fetch page 1 for %s", pa_slug)
            return {"pa_slug": pa_slug, "page_count": None, "cards": []}

        cards = parse_listing_page(html)
        page_count = parse_page_count(html)

        log.info(
            "Scout: %s — %d cards, %s pages",
            pa_slug, len(cards),
            page_count if page_count else "unknown",
        )
        return {"pa_slug": pa_slug, "page_count": page_count, "cards": cards}

    tasks = [fetch_page1(pa) for pa in pa_slugs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    scout_results = []
    for result in results:
        if isinstance(result, BaseException):
            log.error("Scout task exception: %s", result)
            continue
        scout_results.append(result)

    return scout_results


def _build_pa_plans(
    scout_results: list[dict],
    crawl_state: CrawlState,
) -> list[PaPlan]:
    """Build PA plans from scout results, applying PA-level skip logic.

    Sorts PAs by page count descending (broadest first), then processes
    in order. Each PA's page-1 UUIDs are checked against the global set.
    PAs with overlap > threshold are marked skip=True.

    Updates crawl_state.global_uuids as it processes.
    """
    # Sort by page_count descending (None sorts last)
    sorted_results = sorted(
        scout_results,
        key=lambda r: r["page_count"] or 0,
        reverse=True,
    )

    plans = []
    for result in sorted_results:
        pa_slug = result["pa_slug"]
        cards = result["cards"]
        page_count = result["page_count"]

        page1_uuids = {c.uuid for c in cards}
        page1_records = {c.uuid: c.to_dict() for c in cards}

        # Calculate overlap with global set
        if page1_uuids:
            already_known = page1_uuids & crawl_state.global_uuids
            overlap_ratio = len(already_known) / len(page1_uuids)
        else:
            overlap_ratio = 0.0

        skip = overlap_ratio > crawl_state.pa_skip_threshold

        if skip:
            crawl_state.skipped_pas.add(pa_slug)
            log.info(
                "PA skip: %s (%.0f%% overlap, %d/%d known)",
                pa_slug, overlap_ratio * 100,
                len(already_known), len(page1_uuids),
            )
        else:
            # Add this PA's UUIDs to global set
            crawl_state.global_uuids.update(page1_uuids)

        plan = PaPlan(
            slug=pa_slug,
            page_count=page_count,
            page1_records=page1_records,
            overlap_ratio=overlap_ratio,
            skip=skip,
        )
        plans.append(plan)

    return plans
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crawl_listings.py::TestScoutPhase tests/test_crawl_listings.py::TestBuildPaPlans -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add commands/crawl_listings.py tests/test_crawl_listings.py
git commit -m "feat: implement Phase 1 scout — page-1 fetch and PA plan builder"
```

---

### Task 5: Implement Phase 2 — Bulk Fetch

**Files:**
- Modify: `commands/crawl_listings.py` (add `_bulk_fetch_pages`, `_process_bulk_results`)
- Test: `tests/test_crawl_listings.py`

**Step 1: Write failing tests for bulk fetch**

Add to `tests/test_crawl_listings.py`:

```python
class TestBulkFetch:
    @pytest.mark.asyncio
    async def test_fetches_pages_2_through_n(self):
        """Bulk fetch should generate URLs for pages 2-N for non-skipped PAs."""
        fetched_urls = []

        async def fake_httpx_fetch(client, url, referer=None):
            fetched_urls.append(url)
            return "<html>page</html>", "success"

        plans = [
            PaPlan(slug="family-law", page_count=4, skip=False,
                   page1_records={"uuid-1": _fake_record("uuid-1")}),
        ]

        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-2")]):
            from commands.crawl_listings import _bulk_fetch_pages
            state = CrawlState()
            state.global_uuids = {"uuid-1"}
            cf_blocked = await _bulk_fetch_pages(
                httpx_client=AsyncMock(),
                plans=plans,
                state_slug="california",
                city_slug="los-angeles",
                referer="https://example.com/",
                crawl_state=state,
            )

        # Should have fetched pages 2, 3, 4
        page_nums = [u.split("page=")[1] for u in fetched_urls]
        assert "2" in page_nums
        assert "3" in page_nums
        assert "4" in page_nums
        assert "1" not in page_nums

    @pytest.mark.asyncio
    async def test_skipped_pas_not_fetched(self):
        """Skipped PAs should not generate any page fetches."""
        fetched_urls = []

        async def fake_httpx_fetch(client, url, referer=None):
            fetched_urls.append(url)
            return "<html>page</html>", "success"

        plans = [
            PaPlan(slug="family-law", page_count=10, skip=True),
            PaPlan(slug="tax-law", page_count=3, skip=False,
                   page1_records={"uuid-1": _fake_record("uuid-1")}),
        ]

        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-2")]):
            from commands.crawl_listings import _bulk_fetch_pages
            state = CrawlState()
            await _bulk_fetch_pages(
                httpx_client=AsyncMock(),
                plans=plans,
                state_slug="california",
                city_slug="los-angeles",
                referer="https://example.com/",
                crawl_state=state,
            )

        assert not any("family-law" in u for u in fetched_urls)
        assert any("tax-law" in u for u in fetched_urls)

    @pytest.mark.asyncio
    async def test_collects_cf_blocked_urls(self):
        """CF-blocked pages should be collected for browser fallback."""
        async def fake_httpx_fetch(client, url, referer=None):
            return None, "cf_blocked"

        plans = [
            PaPlan(slug="family-law", page_count=3, skip=False,
                   page1_records={"uuid-1": _fake_record("uuid-1")}),
        ]

        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch):
            from commands.crawl_listings import _bulk_fetch_pages
            state = CrawlState()
            cf_blocked = await _bulk_fetch_pages(
                httpx_client=AsyncMock(),
                plans=plans,
                state_slug="california",
                city_slug="los-angeles",
                referer="https://example.com/",
                crawl_state=state,
            )

        assert len(cf_blocked) == 2  # pages 2 and 3
        assert all("family-law" in url for url, _, _ in cf_blocked)

    @pytest.mark.asyncio
    async def test_dedup_threshold_stops_pa(self):
        """When page returns <10% new UUIDs, remaining pages should be skipped."""
        call_count = 0

        async def fake_httpx_fetch(client, url, referer=None):
            nonlocal call_count
            call_count += 1
            return "<html>page</html>", "success"

        # All cards are already known globally
        known_cards = [_make_card(f"uuid-known-{i}") for i in range(20)]

        plans = [
            PaPlan(slug="family-law", page_count=10, skip=False,
                   page1_records={f"uuid-known-{i}": _fake_record(f"uuid-known-{i}") for i in range(20)}),
        ]

        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=known_cards):
            from commands.crawl_listings import _bulk_fetch_pages
            state = CrawlState()
            # Pre-populate global UUIDs so page 2 triggers dedup
            state.global_uuids = {f"uuid-known-{i}" for i in range(20)}
            await _bulk_fetch_pages(
                httpx_client=AsyncMock(),
                plans=plans,
                state_slug="california",
                city_slug="los-angeles",
                referer="https://example.com/",
                crawl_state=state,
            )

        # Should have fetched all 9 pages (2-10) but plan should be marked stopped_early
        assert plans[0].stopped_early is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crawl_listings.py::TestBulkFetch -v`
Expected: FAIL — `_bulk_fetch_pages` doesn't exist.

**Step 3: Implement bulk fetch**

Add to `commands/crawl_listings.py` after `_build_pa_plans`:

```python
async def _bulk_fetch_pages(
    httpx_client: httpx.AsyncClient | None,
    plans: list[PaPlan],
    state_slug: str,
    city_slug: str,
    referer: str,
    crawl_state: CrawlState,
    progress_callback=None,
) -> list[tuple[str, str, int]]:
    """Phase 2: Fetch pages 2-N for all non-skipped PAs in parallel via httpx.

    Fires all requests in parallel, then processes results in PA+page order
    to apply dedup-based early stop correctly.

    Returns:
        List of (url, pa_slug, page_num) tuples for CF-blocked pages
        that need browser fallback.
    """
    sem = asyncio.Semaphore(config.HTTPX_LISTING_CONCURRENT)

    # Generate all (pa_slug, page_num, url) work items
    work_items: list[tuple[str, int, str]] = []
    for plan in plans:
        if plan.skip or plan.page_count is None or plan.page_count <= 1:
            continue
        for page in range(2, plan.page_count + 1):
            url = f"{config.BASE_URL}/{plan.slug}/{state_slug}/{city_slug}/?page={page}"
            work_items.append((plan.slug, page, url))

    if not work_items:
        return []

    # Fetch all pages in parallel
    results_dict: dict[tuple[str, int], tuple[str | None, str]] = {}

    async def fetch_one(pa_slug: str, page: int, url: str):
        async with sem:
            html, status = await _httpx_fetch_listing_page(
                httpx_client, url, referer=referer,
            )
            return pa_slug, page, url, html, status

    tasks = [fetch_one(pa, pg, url) for pa, pg, url in work_items]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in raw_results:
        if isinstance(result, BaseException):
            log.error("Bulk fetch exception: %s", result)
            continue
        pa_slug, page, url, html, status = result
        results_dict[(pa_slug, page)] = (html, status, url)

    # Process results in PA order (by plan), page order
    cf_blocked: list[tuple[str, str, int]] = []
    plan_map = {p.slug: p for p in plans}

    for plan in plans:
        if plan.skip or plan.page_count is None or plan.page_count <= 1:
            continue

        for page in range(2, plan.page_count + 1):
            if plan.stopped_early:
                break
            if crawl_state.should_stop():
                break

            key = (plan.slug, page)
            if key not in results_dict:
                continue

            html, status, url = results_dict[key]

            if status == "cf_blocked":
                cf_blocked.append((url, plan.slug, page))
                continue

            if html is None:
                continue

            cards = parse_listing_page(html)
            if not cards:
                plan.stopped_early = True
                log.info("Bulk: %s p.%d — 0 cards, stopping PA", plan.slug, page)
                break

            page_uuids = {c.uuid for c in cards}

            # Check dedup threshold
            if crawl_state.check_page_dedup(page_uuids, len(cards)):
                globally_new = page_uuids - crawl_state.global_uuids
                log.info(
                    "Bulk: %s p.%d — dedup threshold (%d/%d new), stopping PA",
                    plan.slug, page, len(globally_new), len(cards),
                )
                plan.stopped_early = True
                # Still add the new UUIDs from this page
                for card in cards:
                    if card.uuid not in plan.page1_records:
                        plan.page1_records[card.uuid] = card.to_dict()
                crawl_state.global_uuids.update(page_uuids)
                break

            # Add records
            for card in cards:
                if card.uuid not in plan.page1_records:
                    plan.page1_records[card.uuid] = card.to_dict()
            crawl_state.global_uuids.update(page_uuids)

            if progress_callback:
                progress_callback(pa_slug=plan.slug, page=page, new_count=len(plan.page1_records))

            crawl_state.add_uuids(page_uuids)

    return cf_blocked
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crawl_listings.py::TestBulkFetch -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add commands/crawl_listings.py tests/test_crawl_listings.py
git commit -m "feat: implement Phase 2 bulk fetch with parallel httpx and dedup early stop"
```

---

### Task 6: Implement Phase 3 — Browser Mop-up

**Files:**
- Modify: `commands/crawl_listings.py` (add `_browser_mop_up`)
- Test: `tests/test_crawl_listings.py`

**Step 1: Write failing tests for browser mop-up**

Add to `tests/test_crawl_listings.py`:

```python
class TestBrowserMopUp:
    @pytest.mark.asyncio
    async def test_fetches_cf_blocked_pages(self):
        """Browser mop-up should fetch all CF-blocked URLs."""
        browser_urls = []

        async def fake_fetch(url, referer=None):
            browser_urls.append(url)
            return "<html>browser page</html>"

        mock_pool = _mock_scraper_pool(fake_fetch)

        cf_blocked = [
            ("https://attorneys.superlawyers.com/family-law/ca/la/?page=2", "family-law", 2),
            ("https://attorneys.superlawyers.com/family-law/ca/la/?page=3", "family-law", 3),
        ]
        plans = [PaPlan(slug="family-law", page_count=5, skip=False,
                        page1_records={"uuid-1": _fake_record("uuid-1")})]

        with patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-2")]):
            from commands.crawl_listings import _browser_mop_up
            state = CrawlState()
            await _browser_mop_up(
                browser_client=mock_pool,
                cf_blocked=cf_blocked,
                plans=plans,
                crawl_state=state,
                referer="https://example.com/",
            )

        assert len(browser_urls) == 2

    @pytest.mark.asyncio
    async def test_applies_dedup_during_mop_up(self):
        """Browser mop-up should apply dedup threshold and stop PA early."""
        async def fake_fetch(url, referer=None):
            return "<html>page</html>"

        mock_pool = _mock_scraper_pool(fake_fetch)

        # All cards are already known
        known_cards = [_make_card(f"uuid-{i}") for i in range(20)]

        cf_blocked = [
            ("https://example.com/?page=2", "family-law", 2),
            ("https://example.com/?page=3", "family-law", 3),
        ]
        plans = [PaPlan(slug="family-law", page_count=5, skip=False)]

        with patch("commands.crawl_listings.parse_listing_page", return_value=known_cards):
            from commands.crawl_listings import _browser_mop_up
            state = CrawlState()
            state.global_uuids = {f"uuid-{i}" for i in range(20)}
            await _browser_mop_up(
                browser_client=mock_pool,
                cf_blocked=cf_blocked,
                plans=plans,
                crawl_state=state,
                referer="https://example.com/",
            )

        assert plans[0].stopped_early is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crawl_listings.py::TestBrowserMopUp -v`
Expected: FAIL — `_browser_mop_up` doesn't exist.

**Step 3: Implement browser mop-up**

Add to `commands/crawl_listings.py` after `_bulk_fetch_pages`:

```python
async def _browser_mop_up(
    browser_client: ScraperPool,
    cf_blocked: list[tuple[str, str, int]],
    plans: list[PaPlan],
    crawl_state: CrawlState,
    referer: str,
    progress_callback=None,
) -> None:
    """Phase 3: Fetch CF-blocked pages via browser, applying dedup.

    Processes pages in PA-breadth-then-page order. Applies the same
    dedup threshold as Phase 2.
    """
    if not cf_blocked:
        return

    plan_map = {p.slug: p for p in plans}

    # Sort: broadest PA first, then by page number
    pa_page_counts = {p.slug: (p.page_count or 0) for p in plans}
    cf_blocked_sorted = sorted(
        cf_blocked,
        key=lambda x: (-pa_page_counts.get(x[1], 0), x[2]),
    )

    for url, pa_slug, page_num in cf_blocked_sorted:
        plan = plan_map.get(pa_slug)
        if plan and plan.stopped_early:
            continue
        if crawl_state.should_stop():
            break

        html = await browser_client.fetch(url, referer=referer)
        if html is None:
            log.warning("Browser mop-up: failed to fetch %s", url)
            continue

        cards = parse_listing_page(html)
        if not cards:
            if plan:
                plan.stopped_early = True
            continue

        page_uuids = {c.uuid for c in cards}

        if plan and crawl_state.check_page_dedup(page_uuids, len(cards)):
            globally_new = page_uuids - crawl_state.global_uuids
            log.info(
                "Browser mop-up: %s p.%d — dedup threshold (%d/%d new), stopping PA",
                pa_slug, page_num, len(globally_new), len(cards),
            )
            plan.stopped_early = True
            for card in cards:
                if card.uuid not in plan.page1_records:
                    plan.page1_records[card.uuid] = card.to_dict()
            crawl_state.global_uuids.update(page_uuids)
            continue

        if plan:
            for card in cards:
                if card.uuid not in plan.page1_records:
                    plan.page1_records[card.uuid] = card.to_dict()
        crawl_state.global_uuids.update(page_uuids)
        crawl_state.add_uuids(page_uuids)

        if progress_callback:
            progress_callback(pa_slug=pa_slug, page=page_num,
                              new_count=len(plan.page1_records) if plan else 0)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crawl_listings.py::TestBrowserMopUp -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add commands/crawl_listings.py tests/test_crawl_listings.py
git commit -m "feat: implement Phase 3 browser mop-up with dedup"
```

---

### Task 7: Rewrite `run()` to orchestrate three-phase pipeline

**Files:**
- Modify: `commands/crawl_listings.py:285-433` (replace `run()`)
- Test: `tests/test_crawl_listings.py` (update existing integration tests)

**Step 1: Write a focused integration test for the new run()**

Add to `tests/test_crawl_listings.py`:

```python
class TestThreePhasePipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_produces_listings(self, tmp_path):
        """Three-phase pipeline produces a valid listings.json."""
        pa_path = _make_discovery(tmp_path, ["family-law", "tax-law"])

        page1_cards = [_make_card("uuid-1"), _make_card("uuid-2")]
        page2_cards = [_make_card("uuid-3")]

        call_count = {"n": 0}

        async def fake_httpx_fetch(client, url, referer=None):
            call_count["n"] += 1
            return "<html>page</html>", "success"

        def fake_parse(html):
            # First calls (page 1s) return 2 cards, subsequent return 1
            if call_count["n"] <= 2:
                return page1_cards
            return page2_cards

        with patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", side_effect=fake_parse), \
             patch("commands.crawl_listings.parse_page_count", return_value=2), \
             patch("commands.crawl_listings.httpx") as mock_httpx, \
             patch("commands.crawl_listings.ScraperPool") as MockPool:
            mock_httpx_client = AsyncMock()
            mock_httpx_cm = MagicMock()
            mock_httpx_cm.__aenter__ = AsyncMock(return_value=mock_httpx_client)
            mock_httpx_cm.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_httpx_cm

            mock_pool = AsyncMock()
            mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
            mock_pool.__aexit__ = AsyncMock(return_value=False)
            MockPool.return_value = mock_pool

            result = await run(pa_path)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) >= 1
        assert (tmp_path / "listings.json").exists()
```

**Step 2: Implement new `run()` function**

Replace `run()` in `commands/crawl_listings.py` (lines 285-433) with:

```python
async def run(
    practice_areas_path: str,
    force: bool = False,
    workers: int | None = None,
    pa_filter: list[str] | None = None,
    max_results: int | None = None,
    progress_callback=None,
    browsers: int = 1,
    delay: tuple[float, float] | None = None,
    page_wait: float | None = None,
    no_httpx: bool = False,
) -> str:
    """Crawl listing pages using a three-phase pipeline with dedup.

    Phase 1 (Scout): Fetch page 1 of all PAs, discover page counts,
    build global UUID set, decide which PAs to skip.
    Phase 2 (Bulk Fetch): Fetch remaining pages in parallel via httpx.
    Phase 3 (Browser Mop-up): Re-fetch CF-blocked pages via browser.

    Args:
        practice_areas_path: Path to practice_areas.json.
        force: Ignore checkpoints, re-crawl from scratch.
        workers: Unused (kept for CLI backward compat). Concurrency is
            now controlled by config.HTTPX_LISTING_CONCURRENT.
        pa_filter: Optional list of PA slugs to limit crawling to.
        max_results: Optional cap on unique attorneys to collect.
        progress_callback: Optional callback(pa_slug, page, new_count).
        browsers: Number of browser instances for Phase 3 fallback.
        delay: (min, max) inter-request delay for browser.
        page_wait: Seconds to wait for JS after page load (browser only).
        no_httpx: Disable httpx, use browser for all requests.

    Returns:
        Path to the generated listings.json file.
    """
    with open(practice_areas_path, encoding="utf-8") as f:
        discovery = json.load(f)

    state_slug: str = discovery["state_slug"]
    city_slug: str = discovery["city_slug"]
    practice_areas: list[str] = discovery["practice_areas"]
    data_dir = os.path.dirname(practice_areas_path)
    output_path = os.path.join(data_dir, "listings.json")

    delay_min = delay[0] if delay else None
    delay_max = delay[1] if delay else None

    # Apply PA filter
    if pa_filter:
        unknown = [pa for pa in pa_filter if pa not in practice_areas]
        if unknown:
            log.warning("Unknown practice area slugs (ignored): %s", unknown)
        practice_areas = [pa for pa in practice_areas if pa in pa_filter]
        log.info("Filtered to %d practice areas: %s", len(practice_areas), practice_areas)

    # Force: clean up checkpoint files
    if force:
        _cleanup_pa_files(data_dir)
        log.info("--force: removed existing checkpoint files, starting fresh")

    # Resume: skip PAs with existing per-PA files
    completed_pa_files = _find_completed_pa_files(data_dir)
    already_done = set(completed_pa_files.keys()) & set(practice_areas)
    remaining = [pa for pa in practice_areas if pa not in already_done]

    if already_done:
        log.info(
            "Resuming: %d PAs already completed, %d remaining",
            len(already_done), len(remaining),
        )

    if not remaining:
        log.info("All practice areas already completed, proceeding to merge")
    else:
        referer = f"{config.BASE_URL}/{state_slug}/{city_slug}/"
        crawl_state = CrawlState(max_results=max_results)

        # Set up httpx client
        httpx_client = None
        httpx_cm = None
        if not no_httpx:
            httpx_cm = httpx.AsyncClient(
                proxy=config.PROXY_URL,
                headers=_HTTPX_HEADERS,
                timeout=config.REQUEST_TIMEOUT,
                follow_redirects=True,
            )

        # Set up browser pool for fallback
        browser_pool_cm = ScraperPool(
            num_browsers=browsers,
            delay_min=delay_min,
            delay_max=delay_max,
            page_wait=page_wait,
        )

        try:
            if httpx_cm is not None:
                httpx_client = await httpx_cm.__aenter__()
            browser_client = await browser_pool_cm.__aenter__()

            # Phase 1: Scout
            log.info("=== Phase 1: Scout (%d PAs) ===", len(remaining))
            scout_results = await _scout_all_pas(
                httpx_client=httpx_client,
                browser_client=browser_client,
                pa_slugs=remaining,
                state_slug=state_slug,
                city_slug=city_slug,
                referer=referer,
                no_httpx=no_httpx,
            )

            plans = _build_pa_plans(scout_results, crawl_state)
            active_plans = [p for p in plans if not p.skip]
            log.info(
                "Scout complete: %d PAs to crawl, %d skipped (overlap)",
                len(active_plans), len(crawl_state.skipped_pas),
            )

            # Phase 2: Bulk Fetch (httpx)
            cf_blocked: list[tuple[str, str, int]] = []
            if not no_httpx and httpx_client is not None:
                log.info("=== Phase 2: Bulk Fetch ===")
                cf_blocked = await _bulk_fetch_pages(
                    httpx_client=httpx_client,
                    plans=plans,
                    state_slug=state_slug,
                    city_slug=city_slug,
                    referer=referer,
                    crawl_state=crawl_state,
                    progress_callback=progress_callback,
                )
                log.info(
                    "Bulk fetch complete: %d CF-blocked pages for browser",
                    len(cf_blocked),
                )
            elif no_httpx:
                # Generate all page URLs for browser-only mode
                for plan in plans:
                    if plan.skip or plan.page_count is None or plan.page_count <= 1:
                        continue
                    for page in range(2, plan.page_count + 1):
                        url = f"{config.BASE_URL}/{plan.slug}/{state_slug}/{city_slug}/?page={page}"
                        cf_blocked.append((url, plan.slug, page))

            # Phase 3: Browser Mop-up
            if cf_blocked:
                log.info("=== Phase 3: Browser Mop-up (%d pages) ===", len(cf_blocked))
                await _browser_mop_up(
                    browser_client=browser_client,
                    cf_blocked=cf_blocked,
                    plans=plans,
                    crawl_state=crawl_state,
                    referer=referer,
                    progress_callback=progress_callback,
                )

            # Write per-PA files from plans
            for plan in plans:
                if plan.page1_records:
                    pa_file = os.path.join(data_dir, f"listings_{plan.slug}.json")
                    _atomic_write(pa_file, plan.page1_records)
                    log.info(
                        "%s: %d records written",
                        plan.slug, len(plan.page1_records),
                    )

        finally:
            await browser_pool_cm.__aexit__(None, None, None)
            if httpx_cm is not None:
                await httpx_cm.__aexit__(None, None, None)

    # Merge phase
    all_records = _merge_pa_files(data_dir, max_results=max_results)
    _atomic_write(output_path, all_records)
    _cleanup_pa_files(data_dir)

    log.info(
        "Listings complete: %d unique attorneys written to %s",
        len(all_records), output_path,
    )
    return output_path
```

Also delete `_crawl_one_pa` function (lines 118-237 of the original) since it is replaced by the three-phase pipeline.

**Step 3: Update existing test imports**

Some existing tests reference `_crawl_one_pa` indirectly through `run()`. The existing integration tests (TestPaFilter, TestMaxResults, TestParallelCrawling, TestResume, TestDeduplication, TestHttpxFastPath) need to be updated to mock the new internal functions instead. Replace the mock patterns that use `ScraperPool` as a direct fetcher with mocks for `_httpx_fetch_listing_page` + `parse_listing_page` + `parse_page_count`.

For each existing async test that patches `ScraperPool` and `parse_listing_page`:
- Add `patch("commands.crawl_listings.parse_page_count", return_value=1)` — single-page PAs so bulk fetch has no work.
- Keep existing mock patterns otherwise — the three-phase pipeline still calls `_httpx_fetch_listing_page` and falls back to `ScraperPool.fetch`.

**Step 4: Run the full test suite**

Run: `pytest tests/test_crawl_listings.py -v`
Expected: ALL PASS.

Run: `pytest tests/ -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add commands/crawl_listings.py tests/test_crawl_listings.py
git commit -m "feat: rewrite crawl-listings run() with three-phase pipeline and dedup"
```

---

### Task 8: Update CrawlProgress for three-phase display

**Files:**
- Modify: `progress.py:40-113` (update CrawlProgress)
- Test: `tests/test_progress.py`

**Step 1: Write failing tests**

Add to `tests/test_progress.py`:

```python
class TestCrawlProgressPhases:
    def test_set_phase_updates_description(self):
        cp = CrawlProgress(total_pas=10)
        cp.start()
        cp.set_phase("Scout")
        # Verify phase is tracked
        assert cp._current_phase == "Scout"
        cp.stop()

    def test_set_skipped_count(self):
        cp = CrawlProgress(total_pas=10)
        cp.start()
        cp.set_skipped(3)
        assert cp._skipped_pas == 3
        cp.stop()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_progress.py::TestCrawlProgressPhases -v`
Expected: FAIL — `set_phase` and `set_skipped` don't exist.

**Step 3: Implement phase tracking**

In `progress.py`, update `CrawlProgress.__init__` (line 49) to add:

```python
        self._current_phase: str = ""
        self._skipped_pas: int = 0
```

Add methods after `pa_completed` (after line 106):

```python
    def set_phase(self, phase: str) -> None:
        """Update the current phase indicator."""
        self._current_phase = phase
        if self._progress and self._pa_task_id is not None:
            self._progress.update(
                self._pa_task_id,
                description=f"[{phase}] Practice Areas",
            )

    def set_skipped(self, count: int) -> None:
        """Record number of skipped PAs."""
        self._skipped_pas = count
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_progress.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add progress.py tests/test_progress.py
git commit -m "feat: add phase tracking to CrawlProgress"
```

---

### Task 9: Update README.md and CLAUDE.md

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Step 1: Update README.md**

In `README.md`, replace the "Parallel Crawling" section (lines 59-66) with:

```markdown
## Listing Crawl Pipeline

`crawl-listings` uses a three-phase pipeline optimized for large cities:

1. **Scout** — Fetches page 1 of every practice area in parallel via httpx to discover page counts and collect initial attorney UUIDs.
2. **Bulk Fetch** — Fetches all remaining pages across all PAs in one parallel burst via httpx.
3. **Browser Mop-up** — Re-fetches any Cloudflare-blocked pages through Playwright browser instances.

Three dedup layers reduce redundant work:
- **PA-level skip**: Practice areas where >80% of page-1 attorneys are already known are skipped entirely.
- **Global dedup early stop**: Pagination stops when a page returns zero globally-new attorneys.
- **Threshold early stop**: Pagination stops when <10% of a page's attorneys are globally new.

```bash
# Default (3-phase pipeline with dedup)
python cli.py crawl-listings data/los-angeles_ca/practice_areas.json

# Use 3 browser instances for Cloudflare fallback
python cli.py crawl-listings --browsers 3 data/los-angeles_ca/practice_areas.json

# Browser-only mode (skip httpx)
python cli.py crawl-listings --no-httpx data/los-angeles_ca/practice_areas.json
```
```

In the commands table (lines 51-57), update the crawl-listings row:

```markdown
| crawl-listings | practice_areas.json | listings.json | `--practice-areas`, `--max-results`, `--browsers`, `--delay`, `--page-wait`, `--no-httpx`, `--force` |
```

**Step 2: Update CLAUDE.md**

In `CLAUDE.md`, update the architecture section (around line 86) to replace `crawl-listings` description:

Replace from "`crawl-listings` runs practice areas in parallel" through "Per-PA files act as natural checkpoints for resume." with:

```markdown
`crawl-listings` uses a three-phase pipeline: (1) Scout fetches page 1 of all PAs in parallel to discover page counts and build a global UUID set; (2) Bulk Fetch fires all remaining pages via httpx in one parallel burst with dedup-based early stop; (3) Browser Mop-up re-fetches CF-blocked pages via ScraperPool. Three dedup layers (PA-level skip at >80% overlap, page-level global dedup, threshold early stop at <10% new) eliminate ~50% of page fetches for large cities. Per-PA files act as natural checkpoints for resume.
```

Update the Key Design Decisions section, replacing the "Parallel PA crawling" paragraph with:

```markdown
**Three-phase crawl pipeline**: Phase 1 (Scout) fetches page 1 of all PAs to discover page counts and build a global UUID set. Phase 2 (Bulk Fetch) fires remaining pages in parallel via httpx. Phase 3 (Browser Mop-up) handles CF-blocked pages via ScraperPool. Three dedup layers (PA skip >80% overlap, page-level global dedup, threshold <10% new) reduce page fetches by ~50% for large cities. Config: `PA_SKIP_OVERLAP_THRESHOLD`, `GLOBAL_DEDUP_THRESHOLD`, `HTTPX_LISTING_CONCURRENT`.
```

Update the Rate Limiting section to replace the three-layer concurrency model bullet with:

```markdown
- `HTTPX_LISTING_CONCURRENT = 30` controls httpx parallelism for listing pages
- `ScraperPool` manages browser instances for CF-blocked fallback (`--browsers N`)
- `ScraperClient` semaphore (`MAX_CONCURRENT = 3`) limits concurrent browser tabs
- `asyncio.sleep(random.uniform(2.0, 5.0))` between browser requests
```

**Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: update README and CLAUDE.md for three-phase crawl pipeline"
```

---

### Task 10: Run full test suite and verify

**Files:**
- All modified files

**Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS.

**Step 2: Verify CLI help**

Run: `python cli.py crawl-listings --help`
Expected: Shows all flags including `--browsers`, `--delay`, `--page-wait`, `--no-httpx`.

**Step 3: Verify imports**

Run: `python -c "from commands.crawl_listings import run, CrawlState, PaPlan, _scout_all_pas, _build_pa_plans, _bulk_fetch_pages, _browser_mop_up; print('OK')"`
Expected: `OK`

Run: `python -c "from parsers.listing_parser import parse_page_count; print('OK')"`
Expected: `OK`

**Step 4: Commit any fixups if needed**

---

### Summary of all commits

| # | Message |
|---|---------|
| 1 | `feat: add parse_page_count to extract pagination from listing pages` |
| 2 | `feat: add dedup threshold and httpx concurrency constants` |
| 3 | `feat: add PaPlan dataclass and dedup methods to CrawlState` |
| 4 | `feat: implement Phase 1 scout — page-1 fetch and PA plan builder` |
| 5 | `feat: implement Phase 2 bulk fetch with parallel httpx and dedup early stop` |
| 6 | `feat: implement Phase 3 browser mop-up with dedup` |
| 7 | `feat: rewrite crawl-listings run() with three-phase pipeline and dedup` |
| 8 | `feat: add phase tracking to CrawlProgress` |
| 9 | `docs: update README and CLAUDE.md for three-phase crawl pipeline` |
