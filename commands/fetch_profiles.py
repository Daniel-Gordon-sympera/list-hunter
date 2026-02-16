# commands/fetch_profiles.py
"""Phase 4a: Download raw profile HTML to disk.

Reads listings.json (output of crawl-listings), fetches each attorney's
profile page, and saves the raw HTML to {data_dir}/html/{uuid}.html.

Idempotent: skips UUIDs whose HTML file already exists on disk.
Concurrency is capped by ScraperClient's internal semaphore.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

import config
from http_client import ScraperClient, ScraperPool, is_cloudflare_challenge, is_cloudflare_challenge_response
from progress import is_progress_enabled

log = logging.getLogger(__name__)

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


async def _fetch_one(
    client: ScraperClient,
    uuid: str,
    record: dict,
    html_dir: str,
    index: int,
    total: int,
    on_complete=None,
) -> tuple[str, str]:
    """Fetch a single profile and save its HTML to disk.

    Args:
        on_complete: Optional callback invoked after each fetch completes.

    Returns:
        A (uuid, status) tuple where status is "success" or "failed".
    """
    name = record.get("name", uuid)
    profile_url = record.get("profile_url", "")
    html_path = os.path.join(html_dir, f"{uuid}.html")

    log.info("[%d/%d] Fetching: %s", index, total, name)

    html = await client.fetch(profile_url, referer=config.BASE_URL)

    if html:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        if on_complete:
            on_complete()
        return uuid, "success"

    log.warning("Failed to fetch profile for %s (%s)", name, uuid)
    if on_complete:
        on_complete()
    return uuid, "failed"


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
    from progress import FetchProgress

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
