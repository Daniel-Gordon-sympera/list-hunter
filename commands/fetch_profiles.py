# commands/fetch_profiles.py
"""Phase 4a: Download raw profile HTML to disk.

Reads listings.json (output of crawl-listings), fetches each attorney's
profile page, and saves the raw HTML to {data_dir}/html/{uuid}.html.

Idempotent: skips UUIDs whose HTML file already exists on disk.
Concurrency is capped by asyncio.Semaphore(config.MAX_CONCURRENT).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import config
from http_client import ScraperClient

log = logging.getLogger(__name__)


async def _fetch_one(
    client: ScraperClient,
    semaphore: asyncio.Semaphore,
    uuid: str,
    record: dict,
    html_dir: str,
    index: int,
    total: int,
) -> tuple[str, str]:
    """Fetch a single profile and save its HTML to disk.

    Returns:
        A (uuid, status) tuple where status is "success" or "failed".
    """
    name = record.get("name", uuid)
    profile_url = record.get("profile_url", "")
    html_path = os.path.join(html_dir, f"{uuid}.html")

    log.info("[%d/%d] Fetching: %s", index, total, name)

    async with semaphore:
        html = await client.fetch(profile_url, referer=config.BASE_URL)

    if html:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        return uuid, "success"

    log.warning("Failed to fetch profile for %s (%s)", name, uuid)
    return uuid, "failed"


async def run(listings_path: str) -> str:
    """Fetch profile HTML for every attorney in listings.json.

    Args:
        listings_path: Path to listings.json (dict of {uuid: record_dict}).

    Returns:
        Path to the data directory containing html/ and fetch_status.json.
    """
    with open(listings_path, encoding="utf-8") as f:
        listings: dict[str, dict] = json.load(f)

    data_dir = os.path.dirname(listings_path)
    html_dir = os.path.join(data_dir, "html")
    os.makedirs(html_dir, exist_ok=True)

    # Partition into skipped (already on disk) vs. to-fetch
    to_fetch: dict[str, dict] = {}
    statuses: dict[str, str] = {}

    for uuid, record in listings.items():
        html_path = os.path.join(html_dir, f"{uuid}.html")
        if os.path.exists(html_path):
            statuses[uuid] = "skipped"
        else:
            to_fetch[uuid] = record

    skipped = len(statuses)
    total = len(to_fetch)
    log.info(
        "Profiles to fetch: %d (skipping %d already on disk)", total, skipped
    )

    if to_fetch:
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)

        async with ScraperClient() as client:
            tasks = [
                _fetch_one(
                    client,
                    semaphore,
                    uuid,
                    record,
                    html_dir,
                    index=i + 1,
                    total=total,
                )
                for i, (uuid, record) in enumerate(to_fetch.items())
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                log.error("Task raised exception: %s", result)
                # We cannot determine the uuid from a bare exception,
                # but this path should be rare since _fetch_one catches
                # fetch failures internally.
                continue
            uuid, status = result
            statuses[uuid] = status

    # Compute summary counts
    success = sum(1 for s in statuses.values() if s == "success")
    failed = sum(1 for s in statuses.values() if s == "failed")
    skipped = sum(1 for s in statuses.values() if s == "skipped")

    # Persist fetch status
    status_path = os.path.join(data_dir, "fetch_status.json")
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(statuses, f, indent=2, ensure_ascii=False)

    log.info(
        "Fetch complete: %d success, %d failed, %d skipped",
        success,
        failed,
        skipped,
    )

    return data_dir
