# commands/crawl_listings.py
"""Phase 3: Crawl listing pages, extract profile URLs and pre-fill data.

Reads practice_areas.json (from the discover command), paginates through
every practice-area listing page, parses attorney cards, deduplicates by
UUID, and writes listings.json to the data directory.

Supports parallel crawling of practice areas with configurable worker count.
Each worker writes a per-PA file (listings_{pa_slug}.json) on completion,
then a merge phase combines them into the final listings.json.

Features:
- PA filtering via --practice-areas
- Max results cap via --max-results
- Parallel workers via --workers (default: 3)
- Resume: completed per-PA files are detected and skipped
"""

import asyncio
import glob as globmod
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config
from http_client import ScraperClient
from parsers.listing_parser import parse_listing_page

log = logging.getLogger(__name__)


def _atomic_write(path: str, data: dict) -> None:
    """Write JSON data to *path* atomically via a tmp+rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


@dataclass
class CrawlState:
    """Shared state for parallel PA workers.

    Asyncio-safe (single-threaded event loop — no locks needed).
    """

    max_results: int | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    global_uuids: set = field(default_factory=set)

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


async def _crawl_one_pa(
    client: ScraperClient,
    pa_slug: str,
    state_slug: str,
    city_slug: str,
    data_dir: str,
    referer: str,
    crawl_state: CrawlState,
    pa_index: int,
    total_pas: int,
    progress_callback=None,
) -> tuple[str, dict[str, dict]]:
    """Crawl all pages of a single practice area.

    Writes listings_{pa_slug}.json atomically on completion.

    Returns:
        (pa_slug, {uuid: record_dict}) for the PA.
    """
    pa_records: dict[str, dict] = {}
    page = 1

    while page <= config.MAX_PAGES_PER_CATEGORY:
        if crawl_state.should_stop():
            log.info(
                "[%d/%d] %s: stopping early (max results reached)",
                pa_index, total_pas, pa_slug,
            )
            break

        url = (
            f"{config.BASE_URL}/{pa_slug}/{state_slug}/{city_slug}/"
            f"?page={page}"
        )
        html = await client.fetch(url, referer=referer)

        if html is None:
            log.info(
                "[%d/%d] %s p.%d: fetch returned None, stopping pagination",
                pa_index, total_pas, pa_slug, page,
            )
            break

        cards = parse_listing_page(html)

        if not cards:
            log.info(
                "[%d/%d] %s p.%d: 0 cards, stopping pagination",
                pa_index, total_pas, pa_slug, page,
            )
            break

        new_count = 0
        for record in cards:
            if record.uuid not in pa_records:
                pa_records[record.uuid] = record.to_dict()
                new_count += 1

        log.info(
            "[%d/%d] %s p.%d: %d cards, %d new (PA total: %d)",
            pa_index, total_pas, pa_slug, page,
            len(cards), new_count, len(pa_records),
        )

        if progress_callback:
            progress_callback(
                pa_slug=pa_slug,
                page=page,
                new_count=len(pa_records),
            )

        # No new unique attorneys on this page — stop
        if new_count == 0:
            log.info(
                "[%d/%d] %s: no new attorneys on page %d, stopping",
                pa_index, total_pas, pa_slug, page,
            )
            break

        if crawl_state.add_uuids(set(pa_records.keys())):
            log.info(
                "[%d/%d] %s: max results reached (%d), stopping",
                pa_index, total_pas, pa_slug, len(crawl_state.global_uuids),
            )
            break

        page += 1

    # Write per-PA file atomically
    if pa_records:
        pa_file = os.path.join(data_dir, f"listings_{pa_slug}.json")
        _atomic_write(pa_file, pa_records)
        log.info(
            "[%d/%d] %s: completed (%d records, saved to %s)",
            pa_index, total_pas, pa_slug, len(pa_records),
            os.path.basename(pa_file),
        )

    return pa_slug, pa_records


def _find_completed_pa_files(data_dir: str) -> dict[str, str]:
    """Find existing listings_{pa}.json files and return {pa_slug: filepath}."""
    pattern = os.path.join(data_dir, "listings_*.json")
    result = {}
    for path in globmod.glob(pattern):
        basename = os.path.basename(path)
        # listings_{slug}.json -> slug
        if basename.startswith("listings_") and basename.endswith(".json"):
            slug = basename[len("listings_"):-len(".json")]
            result[slug] = path
    return result


def _merge_pa_files(data_dir: str, max_results: int | None = None) -> dict[str, dict]:
    """Load all per-PA files, merge with UUID dedup, apply max_results trim."""
    completed_files = _find_completed_pa_files(data_dir)
    all_records: dict[str, dict] = {}

    for pa_slug, filepath in sorted(completed_files.items()):
        with open(filepath, encoding="utf-8") as f:
            pa_data = json.load(f)
        for uuid, record in pa_data.items():
            if uuid not in all_records:
                all_records[uuid] = record

    # Apply max_results trim
    if max_results and len(all_records) > max_results:
        all_records = dict(list(all_records.items())[:max_results])

    return all_records


def _cleanup_pa_files(data_dir: str) -> None:
    """Delete all per-PA listing files and crawl_progress.json."""
    completed_files = _find_completed_pa_files(data_dir)
    for filepath in completed_files.values():
        os.remove(filepath)
        log.debug("Removed %s", filepath)

    progress_path = os.path.join(data_dir, "crawl_progress.json")
    if os.path.exists(progress_path):
        os.remove(progress_path)
        log.debug("Removed %s", progress_path)


async def run(
    practice_areas_path: str,
    force: bool = False,
    workers: int | None = None,
    pa_filter: list[str] | None = None,
    max_results: int | None = None,
    progress_callback=None,
) -> str:
    """Crawl listing pages for all practice areas and collect attorney records.

    Args:
        practice_areas_path: Path to practice_areas.json produced by the
            discover command.
        force: If True, ignore any existing checkpoint and re-crawl all
            practice areas from scratch.
        workers: Number of concurrent PA workers (default: config.DEFAULT_PA_WORKERS).
        pa_filter: Optional list of PA slugs to limit crawling to.
        max_results: Optional cap on unique attorneys to collect.
        progress_callback: Optional callback(pa_slug, page, new_count) for progress display.

    Returns:
        Path to the generated listings.json file.
    """
    # Load discovery data
    with open(practice_areas_path, encoding="utf-8") as f:
        discovery = json.load(f)

    state_slug: str = discovery["state_slug"]
    city_slug: str = discovery["city_slug"]
    practice_areas: list[str] = discovery["practice_areas"]
    data_dir = os.path.dirname(practice_areas_path)
    output_path = os.path.join(data_dir, "listings.json")

    num_workers = workers or config.DEFAULT_PA_WORKERS

    # Apply PA filter
    if pa_filter:
        unknown = [pa for pa in pa_filter if pa not in practice_areas]
        if unknown:
            log.warning("Unknown practice area slugs (ignored): %s", unknown)
        practice_areas = [pa for pa in practice_areas if pa in pa_filter]
        log.info("Filtered to %d practice areas: %s", len(practice_areas), practice_areas)

    # Force: clean up any per-PA files and progress
    if force:
        _cleanup_pa_files(data_dir)
        log.info("--force: removed existing checkpoint files, starting fresh")

    # Resume: skip PAs that already have per-PA files
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
        worker_sem = asyncio.Semaphore(num_workers)
        total_pas = len(practice_areas)

        async def bounded_crawl(pa_slug: str, pa_index: int):
            async with worker_sem:
                return await _crawl_one_pa(
                    client, pa_slug, state_slug, city_slug,
                    data_dir, referer, crawl_state,
                    pa_index, total_pas, progress_callback,
                )

        async with ScraperClient() as client:
            tasks = [
                bounded_crawl(pa_slug, practice_areas.index(pa_slug) + 1)
                for pa_slug in remaining
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any exceptions
        for result in results:
            if isinstance(result, BaseException):
                log.error("PA worker raised exception: %s", result)

    # Merge phase: combine all per-PA files
    all_records = _merge_pa_files(data_dir, max_results=max_results)

    # Write final listings.json
    _atomic_write(output_path, all_records)

    # Cleanup per-PA files
    _cleanup_pa_files(data_dir)

    log.info(
        "Listings complete: %d unique attorneys written to %s",
        len(all_records), output_path,
    )
    return output_path
