# fetch-profiles Performance Optimization — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce `fetch-profiles` wall-clock time from ~2.5 hours (5000 profiles) to ~3-5 minutes via httpx fast path, multiple browser instances, and configurable delays.

**Architecture:** Two-phase fetch — Phase 1 tries all profiles with raw `httpx` (fast, ~200ms each), Phase 2 retries CF-blocked profiles through a `ScraperPool` of N Playwright browser instances. `ScraperClient` gains configurable concurrency, delays, and JS page-wait.

**Tech Stack:** Python 3.11+, httpx (new), crawl4ai, asyncio, pytest

**Design doc:** `docs/plans/2026-02-16-fetch-profiles-performance-design.md`

---

### Task 1: Parameterize ScraperClient

**Files:**
- Modify: `http_client.py:90-129`
- Test: `tests/test_http_client.py`

**Step 1: Write failing tests for parameterized ScraperClient**

Add to `tests/test_http_client.py` after the `TestProxyConfig` class (~line 96):

```python
class TestScraperClientParams:
    def test_default_semaphore_uses_config(self):
        """Default ScraperClient uses config.MAX_CONCURRENT for semaphore."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient()
            assert client._semaphore._value == config.MAX_CONCURRENT

    def test_custom_max_concurrent(self):
        """max_concurrent overrides config.MAX_CONCURRENT."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient(max_concurrent=7)
            assert client._semaphore._value == 7

    def test_default_delays_use_config(self):
        """Default ScraperClient uses config delay values."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient()
            assert client._delay_min == config.DELAY_MIN
            assert client._delay_max == config.DELAY_MAX

    def test_custom_delays(self):
        """Custom delay_min/delay_max override config values."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient(delay_min=0.5, delay_max=1.5)
            assert client._delay_min == 0.5
            assert client._delay_max == 1.5

    def test_default_page_wait_uses_config(self):
        """Default ScraperClient uses config.DELAY_BEFORE_RETURN for page_wait."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient()
            assert client._run_config.delay_before_return_html == config.DELAY_BEFORE_RETURN

    def test_custom_page_wait(self):
        """Custom page_wait overrides delay_before_return_html."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient(page_wait=0.5)
            assert client._run_config.delay_before_return_html == 0.5
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_http_client.py::TestScraperClientParams -v`
Expected: FAIL — `ScraperClient.__init__` doesn't accept these params.

**Step 3: Implement parameterized ScraperClient**

In `http_client.py`, replace the `__init__` method (lines 90-129):

```python
def __init__(
    self,
    max_concurrent: int | None = None,
    delay_min: float | None = None,
    delay_max: float | None = None,
    page_wait: float | None = None,
) -> None:
    self._crawler: AsyncWebCrawler | None = None
    self._semaphore = asyncio.Semaphore(max_concurrent or config.MAX_CONCURRENT)
    self._delay_min = delay_min if delay_min is not None else config.DELAY_MIN
    self._delay_max = delay_max if delay_max is not None else config.DELAY_MAX

    os.makedirs(config.BROWSER_PROFILE_DIR, exist_ok=True)

    proxy_config = None
    if config.PROXY_URL:
        proxy_server = f"http://{config.PROXY_SERVER}:{config.PROXY_PORT}"
        proxy_username = config.PROXY_USERNAME
        proxy_password = config.PROXY_PASSWORD
        proxy_config = ProxyConfig(
            server=proxy_server,
            username=proxy_username,
            password=proxy_password,
        )

    use_persistent = not bool(config.PROXY_URL)

    self._browser_config = BrowserConfig(
        headless=True,
        verbose=False,
        enable_stealth=True,
        use_persistent_context=use_persistent,
        user_data_dir=os.path.abspath(config.BROWSER_PROFILE_DIR) if use_persistent else None,
        extra_args=["--disable-blink-features=AutomationControlled"],
        headers=_BROWSER_HEADERS,
        proxy_config=proxy_config,
    )

    actual_page_wait = page_wait if page_wait is not None else config.DELAY_BEFORE_RETURN
    self._run_config = CrawlerRunConfig(
        cache_mode=CacheMode.DISABLED,
        page_timeout=config.REQUEST_TIMEOUT * 1000,
        delay_before_return_html=actual_page_wait,
        override_navigator=True,
    )
```

Also update `fetch()` (line 167) to use instance attrs instead of config:

```python
delay = random.uniform(self._delay_min, self._delay_max)
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_http_client.py -v`
Expected: ALL PASS (new tests + all existing tests still pass).

**Step 5: Commit**

```bash
git add http_client.py tests/test_http_client.py
git commit -m "feat: parameterize ScraperClient with max_concurrent, delays, page_wait"
```

---

### Task 2: Add ScraperPool

**Files:**
- Modify: `http_client.py` (add class after ScraperClient)
- Test: `tests/test_http_client.py`

**Step 1: Write failing tests for ScraperPool**

Add to `tests/test_http_client.py`:

```python
from http_client import ScraperPool


class TestScraperPool:
    @pytest.mark.asyncio
    async def test_pool_creates_n_clients(self):
        """ScraperPool creates num_browsers ScraperClient instances."""
        with patch("http_client.AsyncWebCrawler") as MockCrawler:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockCrawler.return_value = mock_instance

            pool = ScraperPool(num_browsers=3)
            assert len(pool._clients) == 3

    @pytest.mark.asyncio
    async def test_pool_round_robins_fetch(self):
        """Fetch calls distribute across clients round-robin."""
        result = _make_crawl_result(html="<html>ok</html>")
        mock_crawler = _make_mock_crawler(result)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler), \
             patch("http_client.asyncio.sleep", new_callable=AsyncMock):
            async with ScraperPool(num_browsers=2) as pool:
                await pool.fetch("https://example.com/1")
                await pool.fetch("https://example.com/2")
                await pool.fetch("https://example.com/3")

        # Client 0 should get requests 1 and 3, client 1 gets request 2
        assert pool._index == 3

    @pytest.mark.asyncio
    async def test_pool_aenter_starts_all_browsers(self):
        """__aenter__ starts all browser instances."""
        mock_crawler = AsyncMock()
        mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
        mock_crawler.__aexit__ = AsyncMock(return_value=False)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler):
            pool = ScraperPool(num_browsers=3)
            await pool.__aenter__()

        # AsyncWebCrawler should be instantiated 3 times
        assert mock_crawler.__aenter__.await_count == 3

    @pytest.mark.asyncio
    async def test_pool_aexit_closes_all_browsers(self):
        """__aexit__ closes all browser instances."""
        mock_crawler = AsyncMock()
        mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
        mock_crawler.__aexit__ = AsyncMock(return_value=False)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler):
            pool = ScraperPool(num_browsers=3)
            await pool.__aenter__()
            await pool.__aexit__(None, None, None)

        assert mock_crawler.__aexit__.await_count == 3

    @pytest.mark.asyncio
    async def test_pool_passes_params_to_clients(self):
        """ScraperPool passes delay/page_wait params to each client."""
        with patch.object(config, "PROXY_URL", None):
            pool = ScraperPool(num_browsers=2, delay_min=0.5, delay_max=1.0, page_wait=0.3)
            for client in pool._clients:
                assert client._delay_min == 0.5
                assert client._delay_max == 1.0
                assert client._run_config.delay_before_return_html == 0.3

    def test_pool_default_one_browser(self):
        """Default ScraperPool creates 1 browser."""
        with patch.object(config, "PROXY_URL", None):
            pool = ScraperPool()
            assert len(pool._clients) == 1
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_http_client.py::TestScraperPool -v`
Expected: FAIL — `ScraperPool` doesn't exist yet.

**Step 3: Implement ScraperPool**

Add to the end of `http_client.py` (after the `ScraperClient` class):

```python
class ScraperPool:
    """Manages multiple browser instances for parallel fetching.

    Distributes requests round-robin across N ScraperClient instances,
    each owning its own Chromium browser process.  Total max concurrency
    is num_browsers * tabs_per_browser.

    Usage:
        async with ScraperPool(num_browsers=5) as pool:
            html = await pool.fetch("https://...")
    """

    def __init__(
        self,
        num_browsers: int = 1,
        tabs_per_browser: int | None = None,
        delay_min: float | None = None,
        delay_max: float | None = None,
        page_wait: float | None = None,
    ) -> None:
        tabs = tabs_per_browser or config.MAX_CONCURRENT
        self._clients = [
            ScraperClient(
                max_concurrent=tabs,
                delay_min=delay_min,
                delay_max=delay_max,
                page_wait=page_wait,
            )
            for _ in range(num_browsers)
        ]
        self._index = 0

    async def __aenter__(self) -> ScraperPool:
        for client in self._clients:
            await client.__aenter__()
        logger.info("ScraperPool started: %d browser(s)", len(self._clients))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        for client in self._clients:
            await client.__aexit__(exc_type, exc_val, exc_tb)
        logger.info("ScraperPool closed: %d browser(s)", len(self._clients))

    async def fetch(self, url: str, *, referer: str | None = None) -> Optional[str]:
        """Fetch a URL using the next browser in round-robin order."""
        client = self._clients[self._index % len(self._clients)]
        self._index += 1
        return await client.fetch(url, referer=referer)
```

Also update the import in `http_client.py` to export `ScraperPool` — no explicit `__all__` needed, it's imported by name.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_http_client.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add http_client.py tests/test_http_client.py
git commit -m "feat: add ScraperPool for multi-browser parallel fetching"
```

---

### Task 3: Add httpx dependency and config constant

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py:46` (add constant after REQUEST_TIMEOUT)

**Step 1: Add httpx to requirements and config constant**

In `requirements.txt`, add `httpx` after `tenacity`:

```
crawl4ai
beautifulsoup4
lxml
tenacity
httpx
python-slugify
rich
pytest
pytest-asyncio
```

In `config.py`, add after line 46 (`REQUEST_TIMEOUT = 60`):

```python
# httpx fast path
DEFAULT_HTTPX_CONCURRENT = 30  # lightweight, can go higher than browser
```

**Step 2: Install and verify**

Run: `pip install httpx`
Run: `python -c "import httpx; print(httpx.__version__)"`
Expected: Prints version number.

**Step 3: Commit**

```bash
git add requirements.txt config.py
git commit -m "feat: add httpx dependency and DEFAULT_HTTPX_CONCURRENT config"
```

---

### Task 4: Implement httpx sweep function

**Files:**
- Modify: `commands/fetch_profiles.py` (add _httpx_fetch_one, _httpx_sweep)
- Create: `tests/test_fetch_profiles.py`

**Step 1: Write failing tests for httpx sweep**

Create `tests/test_fetch_profiles.py`:

```python
"""Tests for fetch_profiles: httpx sweep, browser fallback, batching."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config


def _fake_listing(uuid, name="Test Attorney"):
    return {
        "uuid": uuid,
        "name": name,
        "profile_url": f"https://profiles.superlawyers.com/california/la/lawyer/test/{uuid}.html",
    }


def _write_listings(tmp_path, listings):
    path = tmp_path / "listings.json"
    path.write_text(json.dumps(listings), encoding="utf-8")
    return str(path)


class TestHttpxFetchOne:
    @pytest.mark.asyncio
    async def test_returns_html_on_success(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>profile content</body></html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "success"
        assert os.path.exists(os.path.join(html_dir, "uuid-1.html"))

    @pytest.mark.asyncio
    async def test_returns_cf_blocked_on_challenge(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><title>Just a moment...</title></html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "cf_blocked"
        assert not os.path.exists(os.path.join(html_dir, "uuid-1.html"))

    @pytest.mark.asyncio
    async def test_returns_failed_on_404(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "failed"

    @pytest.mark.asyncio
    async def test_returns_failed_on_exception(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection error"))

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "failed"


class TestHttpxSweep:
    @pytest.mark.asyncio
    async def test_sweep_returns_statuses(self, tmp_path):
        from commands.fetch_profiles import _httpx_sweep

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)
        to_fetch = {"uuid-1": _fake_listing("uuid-1")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>profile</html>"
        mock_response.headers = {}

        with patch("commands.fetch_profiles.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            statuses, cf_blocked = await _httpx_sweep(
                to_fetch, html_dir, delay_min=0.0, delay_max=0.0,
            )

        assert statuses["uuid-1"] == "success"
        assert len(cf_blocked) == 0

    @pytest.mark.asyncio
    async def test_sweep_collects_cf_blocked(self, tmp_path):
        from commands.fetch_profiles import _httpx_sweep

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)
        to_fetch = {"uuid-cf": _fake_listing("uuid-cf")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><title>Just a moment...</title></html>"
        mock_response.headers = {}

        with patch("commands.fetch_profiles.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            statuses, cf_blocked = await _httpx_sweep(
                to_fetch, html_dir, delay_min=0.0, delay_max=0.0,
            )

        assert "uuid-cf" not in statuses
        assert "uuid-cf" in cf_blocked
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_profiles.py -v`
Expected: FAIL — `_httpx_fetch_one` and `_httpx_sweep` don't exist.

**Step 3: Implement httpx sweep functions**

In `commands/fetch_profiles.py`, add after the imports (line 19):

```python
import httpx

from http_client import ScraperClient, ScraperPool, is_cloudflare_challenge, is_cloudflare_challenge_response
```

Replace the old import line 19: `from http_client import ScraperClient, is_cloudflare_challenge`

Add these functions before `_fetch_one` (after line 21):

```python
_HTTPX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


async def _httpx_fetch_one(
    client: httpx.AsyncClient,
    uuid: str,
    record: dict,
    html_dir: str,
    on_complete=None,
) -> tuple[str, str]:
    """Try fetching a profile with httpx. Returns (uuid, status).

    Status is one of: "success", "cf_blocked", "failed".
    "cf_blocked" means the response was a Cloudflare challenge and should
    be retried with a browser.
    """
    profile_url = record.get("profile_url", "")
    html_path = os.path.join(html_dir, f"{uuid}.html")

    try:
        response = await client.get(profile_url)
    except Exception as exc:
        log.debug("httpx error for %s: %s", uuid, exc)
        if on_complete:
            on_complete()
        return uuid, "failed"

    if response.status_code == 404:
        log.debug("httpx 404 for %s", uuid)
        if on_complete:
            on_complete()
        return uuid, "failed"

    if response.status_code >= 400:
        log.debug("httpx %d for %s", response.status_code, uuid)
        if on_complete:
            on_complete()
        return uuid, "failed"

    html = response.text
    headers = dict(response.headers)

    if is_cloudflare_challenge_response(html, headers):
        log.debug("httpx CF challenge for %s", uuid)
        if on_complete:
            on_complete()
        return uuid, "cf_blocked"

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    if on_complete:
        on_complete()
    return uuid, "success"


async def _httpx_sweep(
    to_fetch: dict[str, dict],
    html_dir: str,
    delay_min: float = 0.0,
    delay_max: float = 0.0,
    max_concurrent: int | None = None,
    on_complete=None,
) -> tuple[dict[str, str], dict[str, dict]]:
    """Try fetching all profiles with httpx. Returns (statuses, cf_blocked).

    statuses: {uuid: "success"|"failed"} for completed profiles.
    cf_blocked: {uuid: record} for profiles needing browser fallback.
    """
    import random

    concurrent = max_concurrent or config.DEFAULT_HTTPX_CONCURRENT
    sem = asyncio.Semaphore(concurrent)
    statuses: dict[str, str] = {}
    cf_blocked: dict[str, dict] = {}

    proxy_url = config.PROXY_URL

    async def bounded_fetch(httpx_client, uuid, record):
        if delay_min > 0 or delay_max > 0:
            await asyncio.sleep(random.uniform(delay_min, delay_max))
        async with sem:
            return await _httpx_fetch_one(
                httpx_client, uuid, record, html_dir, on_complete=on_complete,
            )

    async with httpx.AsyncClient(
        proxy=proxy_url,
        headers=_HTTPX_HEADERS,
        timeout=config.REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as httpx_client:
        tasks = [
            bounded_fetch(httpx_client, uuid, record)
            for uuid, record in to_fetch.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, BaseException):
            log.error("httpx task exception: %s", result)
            continue
        uuid, status = result
        if status == "cf_blocked":
            cf_blocked[uuid] = to_fetch[uuid]
        else:
            statuses[uuid] = status

    log.info(
        "httpx sweep: %d success, %d failed, %d CF-blocked",
        sum(1 for s in statuses.values() if s == "success"),
        sum(1 for s in statuses.values() if s == "failed"),
        len(cf_blocked),
    )
    return statuses, cf_blocked
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_profiles.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add commands/fetch_profiles.py tests/test_fetch_profiles.py
git commit -m "feat: add httpx sweep functions for fast profile fetching"
```

---

### Task 5: Rewrite fetch_profiles.run() — two-phase + batching

**Files:**
- Modify: `commands/fetch_profiles.py:62-161` (rewrite `run()`)
- Test: `tests/test_fetch_profiles.py` (add integration tests)

**Step 1: Write failing tests for new run() signature and two-phase behavior**

Add to `tests/test_fetch_profiles.py`:

```python
class TestWriteStatus:
    def test_writes_json(self, tmp_path):
        from commands.fetch_profiles import _write_status

        path = str(tmp_path / "status.json")
        _write_status(path, {"uuid-1": "success"})

        with open(path) as f:
            data = json.load(f)
        assert data == {"uuid-1": "success"}


class TestRunSignature:
    @pytest.mark.asyncio
    async def test_run_accepts_new_params(self, tmp_path):
        """run() should accept browsers, delay, page_wait, no_httpx params."""
        listings = {"uuid-1": _fake_listing("uuid-1")}
        listings_path = _write_listings(tmp_path, listings)
        (tmp_path / "html").mkdir()

        mock_sweep = AsyncMock(return_value=({"uuid-1": "success"}, {}))

        with patch("commands.fetch_profiles._httpx_sweep", mock_sweep), \
             patch("commands.fetch_profiles.ScraperPool"), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            from commands.fetch_profiles import run
            result = await run(
                listings_path,
                browsers=2,
                delay=(1.0, 2.0),
                page_wait=0.5,
                no_httpx=False,
            )

        assert result == str(tmp_path)

    @pytest.mark.asyncio
    async def test_no_httpx_skips_sweep(self, tmp_path):
        """no_httpx=True should skip httpx sweep and go straight to browser."""
        listings = {"uuid-1": _fake_listing("uuid-1")}
        listings_path = _write_listings(tmp_path, listings)
        (tmp_path / "html").mkdir()

        mock_sweep = AsyncMock()

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value="<html>ok</html>")
        mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
        mock_pool.__aexit__ = AsyncMock(return_value=False)

        with patch("commands.fetch_profiles._httpx_sweep", mock_sweep), \
             patch("commands.fetch_profiles.ScraperPool", return_value=mock_pool), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            from commands.fetch_profiles import run
            await run(listings_path, no_httpx=True)

        mock_sweep.assert_not_awaited()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_profiles.py::TestRunSignature -v`
Expected: FAIL — `run()` doesn't accept new params.

**Step 3: Implement new run() with two-phase fetch and batching**

Replace `run()` in `commands/fetch_profiles.py` (lines 62-161) with:

```python
BATCH_SIZE = 100


def _write_status(path: str, statuses: dict[str, str]) -> None:
    """Write fetch status dict to disk."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(statuses, f, indent=2, ensure_ascii=False)


async def run(
    listings_path: str,
    *,
    force: bool = False,
    retry_cf: bool = False,
    browsers: int = 1,
    delay: tuple[float, float] | None = None,
    page_wait: float | None = None,
    no_httpx: bool = False,
) -> str:
    """Fetch profile HTML for every attorney in listings.json.

    Two-phase approach:
    - Phase 1 (httpx sweep): Try all profiles with raw httpx (fast).
    - Phase 2 (browser mop-up): Retry CF-blocked profiles with ScraperPool.

    Args:
        listings_path: Path to listings.json (dict of {uuid: record_dict}).
        force: Re-download HTML even if files exist on disk.
        retry_cf: Re-download only Cloudflare challenge pages.
        browsers: Number of browser instances for Phase 2 fallback.
        delay: (min, max) inter-request delay in seconds.
        page_wait: Seconds to wait for JS after page load (browser only).
        no_httpx: Skip httpx sweep, use browser for all requests.

    Returns:
        Path to the data directory containing html/ and fetch_status.json.
    """
    with open(listings_path, encoding="utf-8") as f:
        listings: dict[str, dict] = json.load(f)

    data_dir = os.path.dirname(listings_path)
    html_dir = os.path.join(data_dir, "html")
    os.makedirs(html_dir, exist_ok=True)
    status_path = os.path.join(data_dir, "fetch_status.json")

    delay_min = delay[0] if delay else None
    delay_max = delay[1] if delay else None

    # Partition into skipped vs to-fetch
    to_fetch: dict[str, dict] = {}
    statuses: dict[str, str] = {}

    for uuid, record in listings.items():
        html_path = os.path.join(html_dir, f"{uuid}.html")
        if not force and os.path.exists(html_path):
            if retry_cf:
                with open(html_path, encoding="utf-8", errors="replace") as hf:
                    head = hf.read(500)
                if is_cloudflare_challenge(head):
                    to_fetch[uuid] = record
                    continue
            statuses[uuid] = "skipped"
        else:
            to_fetch[uuid] = record

    skipped = len(statuses)
    total = len(to_fetch)
    log.info("Profiles to fetch: %d (skipping %d already on disk)", total, skipped)

    if not to_fetch:
        _write_status(status_path, statuses)
        log.info("Nothing to fetch.")
        return data_dir

    # Set up progress bar
    from progress import FetchProgress, is_progress_enabled

    fetch_progress = None
    on_complete = None
    if is_progress_enabled():
        fetch_progress = FetchProgress(total=total)
        fetch_progress.start()
        on_complete = fetch_progress.advance

    try:
        browser_targets = to_fetch  # default: all go to browser

        # Phase 1: httpx sweep (unless disabled)
        if not no_httpx:
            log.info("Phase 1: httpx sweep (%d profiles)", len(to_fetch))
            httpx_statuses, cf_blocked = await _httpx_sweep(
                to_fetch,
                html_dir,
                delay_min=delay_min or config.DELAY_MIN,
                delay_max=delay_max or config.DELAY_MAX,
                on_complete=on_complete,
            )
            statuses.update(httpx_statuses)
            _write_status(status_path, statuses)
            browser_targets = cf_blocked
            log.info(
                "Phase 1 complete: %d via httpx, %d need browser fallback",
                len(httpx_statuses), len(cf_blocked),
            )

        # Phase 2: browser mop-up (for CF-blocked or all if no_httpx)
        if browser_targets:
            log.info("Phase 2: browser fallback (%d profiles, %d browser(s))",
                     len(browser_targets), browsers)

            async with ScraperPool(
                num_browsers=browsers,
                delay_min=delay_min,
                delay_max=delay_max,
                page_wait=page_wait,
            ) as pool:
                items = list(browser_targets.items())
                batch_size = max(BATCH_SIZE, browsers * 3 * 5)

                for batch_start in range(0, len(items), batch_size):
                    batch = items[batch_start:batch_start + batch_size]
                    tasks = [
                        _fetch_one(
                            pool, uuid, record, html_dir,
                            index=batch_start + i + 1,
                            total=len(browser_targets),
                            on_complete=on_complete,
                        )
                        for i, (uuid, record) in enumerate(batch)
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    for result in results:
                        if isinstance(result, BaseException):
                            log.error("Task raised exception: %s", result)
                            continue
                        uuid, status = result
                        statuses[uuid] = status

                    _write_status(status_path, statuses)
    finally:
        if fetch_progress:
            fetch_progress.stop()

    # Final summary
    success = sum(1 for s in statuses.values() if s == "success")
    failed = sum(1 for s in statuses.values() if s == "failed")
    skipped = sum(1 for s in statuses.values() if s == "skipped")
    log.info(
        "Fetch complete: %d success, %d failed, %d skipped",
        success, failed, skipped,
    )

    return data_dir
```

**Step 4: Run all tests**

Run: `pytest tests/test_fetch_profiles.py -v`
Expected: ALL PASS.

Run: `pytest tests/ -v`
Expected: ALL PASS (including existing tests — backward compat check).

**Step 5: Commit**

```bash
git add commands/fetch_profiles.py tests/test_fetch_profiles.py
git commit -m "feat: two-phase fetch (httpx + browser) with batching and intermediate status"
```

---

### Task 6: CLI flags for fetch-profiles

**Files:**
- Modify: `cli.py:88-103` (cmd_fetch_profiles) and `cli.py:200-221` (subparser)
- Test: `tests/test_cli.py`

**Step 1: Write failing tests for new CLI flags**

Add to `tests/test_cli.py` in the `TestHelpOutput` class:

```python
    def test_fetch_profiles_help_shows_new_args(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["cli.py", "fetch-profiles", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--browsers" in captured.out
        assert "--delay" in captured.out
        assert "--page-wait" in captured.out
        assert "--no-httpx" in captured.out
```

Add to `TestSubcommandWiring`:

```python
    def test_cmd_fetch_profiles_passes_new_params(self):
        mock_run = AsyncMock(return_value="/data/html")
        args = MagicMock()
        args.input = "/data/listings.json"
        args.force = False
        args.retry_cf = False
        args.verbose = False
        args.browsers = 5
        args.delay = "1.0,2.0"
        args.page_wait = 0.5
        args.no_httpx = False

        with patch("commands.fetch_profiles.run", mock_run), \
             patch("cli.setup_logging"):
            cmd_fetch_profiles(args)
            mock_run.assert_awaited_once_with(
                "/data/listings.json",
                force=False,
                retry_cf=False,
                browsers=5,
                delay=(1.0, 2.0),
                page_wait=0.5,
                no_httpx=False,
            )

    def test_cmd_fetch_profiles_default_params(self):
        mock_run = AsyncMock(return_value="/data/html")
        args = MagicMock()
        args.input = "/data/listings.json"
        args.force = False
        args.retry_cf = False
        args.verbose = False
        args.browsers = None
        args.delay = None
        args.page_wait = None
        args.no_httpx = False

        with patch("commands.fetch_profiles.run", mock_run), \
             patch("cli.setup_logging"):
            cmd_fetch_profiles(args)
            mock_run.assert_awaited_once_with(
                "/data/listings.json",
                force=False,
                retry_cf=False,
                browsers=1,
                delay=None,
                page_wait=None,
                no_httpx=False,
            )
```

Add to `TestMainDispatch`:

```python
    def test_main_fetch_profiles_new_flags(self):
        with patch("sys.argv", [
            "cli.py", "fetch-profiles",
            "--browsers", "5",
            "--delay", "1.0,2.0",
            "--page-wait", "0.5",
            "--no-httpx",
            "/path/to/listings.json",
        ]), patch("cli.cmd_fetch_profiles") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.browsers == 5
            assert args.delay == "1.0,2.0"
            assert args.page_wait == 0.5
            assert args.no_httpx is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestHelpOutput::test_fetch_profiles_help_shows_new_args -v`
Expected: FAIL — flags don't exist yet.

**Step 3: Implement CLI changes**

In `cli.py`, replace `cmd_fetch_profiles` (lines 88-102):

```python
def cmd_fetch_profiles(args: argparse.Namespace) -> None:
    """Run the fetch-profiles phase: download raw profile HTML."""
    data_dir = os.path.dirname(args.input)
    setup_logging(
        verbose=args.verbose,
        data_dir=data_dir,
        command_name="fetch-profiles",
    )

    from commands import fetch_profiles

    # Parse delay
    delay = None
    if args.delay:
        parts = args.delay.split(",")
        if len(parts) != 2:
            print("Error: --delay must be MIN,MAX (e.g. 1.0,3.0)")
            raise SystemExit(1)
        delay = (float(parts[0].strip()), float(parts[1].strip()))
        if delay[0] > delay[1]:
            print("Error: --delay MIN must be <= MAX")
            raise SystemExit(1)

    result = asyncio.run(
        fetch_profiles.run(
            args.input,
            force=args.force,
            retry_cf=args.retry_cf,
            browsers=args.browsers or 1,
            delay=delay,
            page_wait=args.page_wait,
            no_httpx=args.no_httpx,
        )
    )
    print(f"Output: {result}")
```

In `cli.py`, replace the fetch-profiles subparser section (lines 200-221):

```python
    # -- fetch-profiles --
    sp_fetch = subparsers.add_parser(
        "fetch-profiles",
        help="Download raw profile HTML to disk",
    )
    sp_fetch.add_argument(
        "input",
        help="Path to listings.json from the crawl-listings step",
    )
    sp_fetch.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Re-download HTML even if files already exist on disk",
    )
    sp_fetch.add_argument(
        "--retry-cf",
        action="store_true",
        default=False,
        help="Re-download only HTML files that are Cloudflare challenge pages",
    )
    sp_fetch.add_argument(
        "--browsers",
        type=int,
        default=None,
        help="Number of browser instances for fallback (default: 1)",
    )
    sp_fetch.add_argument(
        "--delay",
        default=None,
        help="Delay range as MIN,MAX seconds (default: 2.0,5.0). Example: --delay 1.0,3.0",
    )
    sp_fetch.add_argument(
        "--page-wait",
        type=float,
        default=None,
        help="Seconds to wait for JS after page load (default: 2.0)",
    )
    sp_fetch.add_argument(
        "--no-httpx",
        action="store_true",
        default=False,
        help="Disable httpx fast path, use browser for all requests",
    )
    sp_fetch.set_defaults(func=cmd_fetch_profiles)
```

**Step 4: Run all CLI tests**

Run: `pytest tests/test_cli.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add cli.py tests/test_cli.py
git commit -m "feat: add --browsers, --delay, --page-wait, --no-httpx flags to fetch-profiles"
```

---

### Task 7: Thread params through main.py pipeline

**Files:**
- Modify: `main.py:22-73`

**Step 1: Update run_pipeline signature and fetch_profiles call**

In `main.py`, update the `run_pipeline` function signature (line 22-28) to add fetch params:

```python
async def run_pipeline(
    location: str,
    output_dir: str | None = None,
    verbose: bool = False,
    workers: int | None = None,
    pa_filter: list[str] | None = None,
    max_results: int | None = None,
    fetch_browsers: int = 1,
    fetch_delay: tuple[float, float] | None = None,
    fetch_page_wait: float | None = None,
    fetch_no_httpx: bool = False,
) -> str:
```

Update the Phase 4a call (line 64):

```python
    # Phase 4a: Fetch profile HTML
    logger.info("=== Phase 4a: Fetch profiles ===")
    data_dir = await fetch_profiles.run(
        listings_path,
        browsers=fetch_browsers,
        delay=fetch_delay,
        page_wait=fetch_page_wait,
        no_httpx=fetch_no_httpx,
    )
```

**Step 2: Run existing tests to verify nothing broke**

Run: `pytest tests/ -v`
Expected: ALL PASS.

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: thread fetch-profiles performance params through run_pipeline"
```

---

### Task 8: Add ETA to progress bar

**Files:**
- Modify: `progress.py:115-159`
- Test: `tests/test_progress.py`

**Step 1: Write test for TimeRemainingColumn**

Add to `tests/test_progress.py`:

```python
class TestFetchProgressETA:
    def test_progress_bar_includes_eta(self):
        """FetchProgress should include TimeRemainingColumn."""
        fp = FetchProgress(total=100)
        fp.start()
        # Verify the progress object has TimeRemainingColumn
        column_types = [type(c).__name__ for c in fp._progress.columns]
        assert "TimeRemainingColumn" in column_types
        fp.stop()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_progress.py::TestFetchProgressETA -v`
Expected: FAIL — no `TimeRemainingColumn` in columns.

**Step 3: Implement ETA column**

In `progress.py`, add `TimeRemainingColumn` to the import (line 18):

```python
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
```

In `FetchProgress.start()` (line 130), add `TimeRemainingColumn()` after `TimeElapsedColumn()`:

```python
    self._progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=Console(stderr=True),
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_progress.py -v`
Expected: ALL PASS.

**Step 5: Commit**

```bash
git add progress.py tests/test_progress.py
git commit -m "feat: add ETA column to fetch-profiles progress bar"
```

---

### Task 9: Final integration test — full test suite

**Files:**
- All modified files

**Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS. Every existing test still passes, plus all new tests.

**Step 2: Verify CLI help**

Run: `python cli.py fetch-profiles --help`
Expected output includes: `--browsers`, `--delay`, `--page-wait`, `--no-httpx`

**Step 3: Commit any remaining fixups**

If any tests fail, fix and commit.

---

### Summary of all commits

| # | Message |
|---|---------|
| 1 | `feat: parameterize ScraperClient with max_concurrent, delays, page_wait` |
| 2 | `feat: add ScraperPool for multi-browser parallel fetching` |
| 3 | `feat: add httpx dependency and DEFAULT_HTTPX_CONCURRENT config` |
| 4 | `feat: add httpx sweep functions for fast profile fetching` |
| 5 | `feat: two-phase fetch (httpx + browser) with batching and intermediate status` |
| 6 | `feat: add --browsers, --delay, --page-wait, --no-httpx flags to fetch-profiles` |
| 7 | `feat: thread fetch-profiles performance params through run_pipeline` |
| 8 | `feat: add ETA column to fetch-profiles progress bar` |
