# http_client.py
"""Async HTTP client wrapping Crawl4AI's browser-based crawler.

Provides a ScraperClient async context manager that command modules use
for fetching pages.  Uses a real browser (Playwright under the hood) to
bypass Cloudflare protection on superlawyers.com.

Usage:
    async with ScraperClient() as client:
        html = await client.fetch("https://profiles.superlawyers.com/...")
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Optional

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.async_configs import ProxyConfig
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
    RetryError,
)

import config

logger = logging.getLogger(__name__)

_CLOUDFLARE_MARKERS = (
    "<title>Just a moment...</title>",
    "challenge-platform",
    "Verifying you are human",
)


def is_cloudflare_challenge(html: str) -> bool:
    """Return True if *html* looks like a Cloudflare challenge page."""
    return any(marker in html for marker in _CLOUDFLARE_MARKERS)


class FetchError(Exception):
    """Raised when a page fetch fails and should be retried."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ScraperClient:
    """Async context manager providing rate-limited, retried page fetching.

    Keeps a single browser instance alive across all requests.  Limits
    concurrency with an asyncio.Semaphore and adds a random delay before
    each request to avoid hammering the server.
    """

    def __init__(self) -> None:
        self._crawler: AsyncWebCrawler | None = None
        self._semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)

        os.makedirs(config.BROWSER_PROFILE_DIR, exist_ok=True)

        proxy_config = None
        if config.PROXY_URL:
            proxy_config = ProxyConfig.from_string(config.PROXY_URL)

        self._browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            enable_stealth=True,
            use_persistent_context=True,
            user_data_dir=os.path.abspath(config.BROWSER_PROFILE_DIR),
            extra_args=["--disable-blink-features=AutomationControlled"],
            proxy_config=proxy_config,
        )
        self._run_config = CrawlerRunConfig(
            cache_mode=CacheMode.DISABLED,
            page_timeout=config.REQUEST_TIMEOUT * 1000,  # ms
            delay_before_return_html=config.DELAY_BEFORE_RETURN,
            override_navigator=True,
        )

    async def __aenter__(self) -> ScraperClient:
        self._crawler = AsyncWebCrawler(config=self._browser_config)
        await self._crawler.__aenter__()
        if config.PROXY_URL:
            logger.info("Browser started (headless, proxy enabled)")
        else:
            logger.info("Browser started (headless, no proxy)")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._crawler is not None:
            await self._crawler.__aexit__(exc_type, exc_val, exc_tb)
            self._crawler = None
            logger.info("Browser closed")

    async def fetch(self, url: str, *, referer: str | None = None) -> Optional[str]:
        """Fetch a URL and return raw HTML, or None on permanent failure.

        Applies rate limiting (random delay), concurrency control (semaphore),
        and retry logic (exponential backoff).  Returns None for 404 responses
        instead of retrying.

        Args:
            url: The page URL to fetch.
            referer: Optional Referer header value for the request.

        Returns:
            The raw HTML string on success, or None if the page was not found
            or all retries were exhausted.
        """
        async with self._semaphore:
            # Rate-limit: random sleep before each request
            delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
            logger.debug("Sleeping %.1fs before request to %s", delay, url)
            await asyncio.sleep(delay)

            try:
                return await self._fetch_with_retry(url, referer=referer)
            except RetryError as exc:
                logger.error(
                    "All %d retries exhausted for %s: %s",
                    config.MAX_RETRIES,
                    url,
                    exc,
                )
                return None

    async def _fetch_with_retry(
        self, url: str, *, referer: str | None = None
    ) -> Optional[str]:
        """Inner fetch wrapped with tenacity retry logic.

        The retry decorator is applied dynamically so that config values
        are read at call time rather than import time.
        """

        @retry(
            retry=retry_if_exception_type(FetchError),
            stop=stop_after_attempt(config.MAX_RETRIES),
            wait=wait_exponential(
                multiplier=config.RETRY_BACKOFF_BASE,
                min=config.RETRY_BACKOFF_BASE,
                max=config.RETRY_BACKOFF_BASE ** config.MAX_RETRIES,
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        async def _do_fetch() -> Optional[str]:
            return await self._single_fetch(url, referer=referer)

        return await _do_fetch()

    async def _single_fetch(
        self, url: str, *, referer: str | None = None
    ) -> Optional[str]:
        """Execute one crawl attempt and return HTML or raise FetchError."""
        if self._crawler is None:
            raise RuntimeError(
                "ScraperClient must be used as an async context manager"
            )

        logger.info("Fetching: %s", url)

        try:
            result = await self._crawler.arun(url=url, config=self._run_config)
        except Exception as exc:
            logger.warning("Crawler exception for %s: %s", url, exc)
            raise FetchError(f"Crawler exception: {exc}") from exc

        status = result.status_code
        logger.info("Response: %s -> %s", url, status)

        # 404: page doesn't exist, no point retrying
        if status == 404:
            logger.warning("404 Not Found: %s", url)
            return None

        # Success — but reject Cloudflare challenge pages
        if result.success and result.html:
            if is_cloudflare_challenge(result.html):
                logger.warning("Cloudflare challenge detected for %s", url)
                raise FetchError("Cloudflare challenge detected")
            logger.debug(
                "Fetched %s: %d chars", url, len(result.html)
            )
            return result.html

        # Other failure: retry
        error_msg = result.error_message or f"HTTP {status}"
        logger.warning("Fetch failed for %s: %s", url, error_msg)
        raise FetchError(error_msg, status_code=status)
