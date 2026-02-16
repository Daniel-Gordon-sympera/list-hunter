# tests/test_http_client.py
"""Tests for http_client: proxy wiring, Cloudflare detection, fetch flow, and concurrency."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from crawl4ai.async_configs import ProxyConfig

import config
from http_client import (
    FetchError,
    ScraperClient,
    is_cloudflare_challenge,
    is_cloudflare_challenge_response,
    _CLOUDFLARE_MARKERS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_crawl_result(*, success=True, html="<html>ok</html>", status_code=200,
                       response_headers=None, error_message=None):
    """Build a fake CrawlResult-like object."""
    result = MagicMock()
    result.success = success
    result.html = html
    result.status_code = status_code
    result.response_headers = response_headers or {}
    result.error_message = error_message
    return result


def _make_mock_crawler(result):
    """Build a mock AsyncWebCrawler that returns *result* from arun()."""
    mock_crawler = AsyncMock()
    mock_crawler.arun = AsyncMock(return_value=result)
    mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
    mock_crawler.__aexit__ = AsyncMock(return_value=False)
    return mock_crawler


# ---------------------------------------------------------------------------
# Proxy config tests (existing)
# ---------------------------------------------------------------------------

class TestProxyConfig:
    def test_no_proxy_when_env_unset(self):
        """No proxy_config when PROXY_URL is None; persistent context enabled."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient()
            assert client._browser_config.proxy_config is None
            assert client._browser_config.use_persistent_context is True
            assert client._browser_config.user_data_dir is not None

    def test_proxy_configured_when_env_set(self):
        """proxy_config wired to BrowserConfig when PROXY_URL is set."""
        with patch.object(config, "PROXY_URL", "http://user:pass@proxy.example.com:8080"), \
             patch.object(config, "PROXY_SERVER", "proxy.example.com"), \
             patch.object(config, "PROXY_PORT", "8080"), \
             patch.object(config, "PROXY_USERNAME", "user"), \
             patch.object(config, "PROXY_PASSWORD", "pass"):
            client = ScraperClient()
            pc = client._browser_config.proxy_config
            assert isinstance(pc, ProxyConfig)
            assert pc.server == "http://proxy.example.com:8080"
            assert pc.username == "user"
            assert pc.password == "pass"

    def test_persistent_context_disabled_with_proxy(self):
        """Persistent context must be off when proxy is configured to avoid ERR_NO_SUPPORTED_PROXIES."""
        with patch.object(config, "PROXY_URL", "http://user:pass@proxy.example.com:8080"), \
             patch.object(config, "PROXY_SERVER", "proxy.example.com"), \
             patch.object(config, "PROXY_PORT", "8080"), \
             patch.object(config, "PROXY_USERNAME", "user"), \
             patch.object(config, "PROXY_PASSWORD", "pass"):
            client = ScraperClient()
            assert client._browser_config.use_persistent_context is False
            assert client._browser_config.user_data_dir is None

    def test_brightdata_proxy_format_parsed(self):
        """Bright Data residential proxy URL parses correctly."""
        url = "http://brd-customer-hl_1a29887d-zone-residential_proxy1:pi63dagbslkk@brd.superproxy.io:33335"
        with patch.object(config, "PROXY_URL", url), \
             patch.object(config, "PROXY_SERVER", "brd.superproxy.io"), \
             patch.object(config, "PROXY_PORT", "33335"), \
             patch.object(config, "PROXY_USERNAME", "brd-customer-hl_1a29887d-zone-residential_proxy1"), \
             patch.object(config, "PROXY_PASSWORD", "pi63dagbslkk"):
            client = ScraperClient()
            pc = client._browser_config.proxy_config
            assert pc.server == "http://brd.superproxy.io:33335"
            assert pc.username == "brd-customer-hl_1a29887d-zone-residential_proxy1"
            assert pc.password == "pi63dagbslkk"


# ---------------------------------------------------------------------------
# ScraperClient parameter tests
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Cloudflare detection tests
# ---------------------------------------------------------------------------

class TestCloudflareDetection:
    def test_detects_challenge_page(self):
        assert is_cloudflare_challenge("<title>Just a moment...</title>")

    def test_clean_html_not_flagged(self):
        assert not is_cloudflare_challenge("<html><body>Hello</body></html>")

    @pytest.mark.parametrize("marker", list(_CLOUDFLARE_MARKERS))
    def test_each_marker_triggers_detection(self, marker):
        """Every marker in _CLOUDFLARE_MARKERS should trigger detection."""
        html = f"<html><body>{marker}</body></html>"
        assert is_cloudflare_challenge(html)

    def test_cf_response_clean_html_and_headers_not_flagged(self):
        """Normal HTML + normal headers should not trigger."""
        assert not is_cloudflare_challenge_response(
            "<html>OK</html>", {"content-type": "text/html"}
        )

    def test_cf_response_detects_html_markers(self):
        """HTML markers should trigger even without headers."""
        assert is_cloudflare_challenge_response(
            "<html>cf-turnstile</html>", None
        )

    def test_cf_response_detects_cf_mitigated_header(self):
        """cf-mitigated: challenge header should trigger detection."""
        assert is_cloudflare_challenge_response(
            "<html>clean</html>",
            {"cf-mitigated": "challenge"},
        )

    def test_cf_response_detects_cf_mitigated_case_insensitive(self):
        """cf-mitigated header detection is case-insensitive on value."""
        assert is_cloudflare_challenge_response(
            "<html>clean</html>",
            {"cf-mitigated": "Challenge"},
        )

    def test_cf_response_no_headers_clean_html(self):
        """None headers with clean HTML should not trigger."""
        assert not is_cloudflare_challenge_response("<html>clean</html>", None)


# ---------------------------------------------------------------------------
# Fetch flow tests
# ---------------------------------------------------------------------------

class TestFetchFlow:
    @pytest.mark.asyncio
    async def test_fetch_returns_html_on_success(self):
        """Successful arun returns HTML string."""
        result = _make_crawl_result(html="<html>content</html>")
        mock_crawler = _make_mock_crawler(result)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler), \
             patch("http_client.asyncio.sleep", new_callable=AsyncMock):
            async with ScraperClient() as client:
                html = await client.fetch("https://example.com")

        assert html == "<html>content</html>"

    @pytest.mark.asyncio
    async def test_fetch_returns_none_on_404(self):
        """404 status_code returns None, no retry."""
        result = _make_crawl_result(success=True, html="", status_code=404)
        mock_crawler = _make_mock_crawler(result)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler), \
             patch("http_client.asyncio.sleep", new_callable=AsyncMock):
            async with ScraperClient() as client:
                html = await client.fetch("https://example.com/missing")

        assert html is None
        # Should only be called once (no retry on 404)
        assert mock_crawler.arun.await_count == 1

    @pytest.mark.asyncio
    async def test_fetch_returns_none_after_retries_exhausted(self):
        """All retry attempts fail -> returns None (not exception)."""
        result = _make_crawl_result(
            success=False, html="", status_code=500,
            error_message="Internal Server Error",
        )
        mock_crawler = _make_mock_crawler(result)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler), \
             patch("http_client.asyncio.sleep", new_callable=AsyncMock), \
             patch.object(config, "MAX_RETRIES", 2), \
             patch.object(config, "RETRY_BACKOFF_BASE", 0.01):
            async with ScraperClient() as client:
                html = await client.fetch("https://example.com/fail")

        assert html is None
        # Should have retried MAX_RETRIES times
        assert mock_crawler.arun.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_raises_on_cf_challenge_then_retries(self):
        """CF challenge HTML triggers FetchError -> retry. All retries CF -> None."""
        cf_html = "<html><title>Just a moment...</title></html>"
        result = _make_crawl_result(html=cf_html)
        mock_crawler = _make_mock_crawler(result)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler), \
             patch("http_client.asyncio.sleep", new_callable=AsyncMock), \
             patch.object(config, "MAX_RETRIES", 2), \
             patch.object(config, "RETRY_BACKOFF_BASE", 0.01):
            async with ScraperClient() as client:
                html = await client.fetch("https://example.com/cf")

        assert html is None
        # Should have retried
        assert mock_crawler.arun.await_count == 2

    @pytest.mark.asyncio
    async def test_single_fetch_raises_on_failure(self):
        """Non-success result raises FetchError with status_code."""
        result = _make_crawl_result(
            success=False, html="", status_code=503,
            error_message="Service Unavailable",
        )
        mock_crawler = _make_mock_crawler(result)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler):
            client = ScraperClient()
            client._crawler = mock_crawler
            with pytest.raises(FetchError) as exc_info:
                await client._single_fetch("https://example.com/503")
            assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Semaphore / sleep ordering test
# ---------------------------------------------------------------------------

class TestSleepSemaphoreOrdering:
    @pytest.mark.asyncio
    async def test_sleep_runs_outside_semaphore(self):
        """Sleep should happen before semaphore acquire, not while holding it."""
        result = _make_crawl_result()
        mock_crawler = _make_mock_crawler(result)

        events = []

        original_semaphore_cls = asyncio.Semaphore

        class TrackedSemaphore(original_semaphore_cls):
            async def __aenter__(self):
                events.append("semaphore_acquire")
                return await super().__aenter__()

            async def __aexit__(self, *args):
                events.append("semaphore_release")
                return await super().__aexit__(*args)

        async def tracked_sleep(duration):
            events.append("sleep")

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler), \
             patch("http_client.asyncio.sleep", side_effect=tracked_sleep):
            client = ScraperClient()
            client._semaphore = TrackedSemaphore(config.MAX_CONCURRENT)
            client._crawler = mock_crawler
            await client._crawler.__aenter__()
            await client.fetch("https://example.com")

        # Sleep must come before semaphore_acquire
        assert events.index("sleep") < events.index("semaphore_acquire")


# ---------------------------------------------------------------------------
# Context manager lifecycle test
# ---------------------------------------------------------------------------

class TestContextManagerLifecycle:
    @pytest.mark.asyncio
    async def test_aenter_starts_crawler_aexit_closes(self):
        """__aenter__ starts crawler, __aexit__ closes it and sets to None."""
        mock_crawler = AsyncMock()
        mock_crawler.__aenter__ = AsyncMock(return_value=mock_crawler)
        mock_crawler.__aexit__ = AsyncMock(return_value=False)

        with patch("http_client.AsyncWebCrawler", return_value=mock_crawler):
            client = ScraperClient()
            assert client._crawler is None

            await client.__aenter__()
            assert client._crawler is not None
            mock_crawler.__aenter__.assert_awaited_once()

            await client.__aexit__(None, None, None)
            assert client._crawler is None
            mock_crawler.__aexit__.assert_awaited_once()
