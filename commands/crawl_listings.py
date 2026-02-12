# commands/crawl_listings.py
"""Phase 3: Crawl listing pages, extract profile URLs and pre-fill data.

Reads practice_areas.json (from the discover command), paginates through
every practice-area listing page, parses attorney cards, deduplicates by
UUID, and writes listings.json to the data directory.

Checkpoints progress after each practice area so interrupted runs can
resume without re-crawling completed areas.
"""

import json
import logging
import os
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


def _write_checkpoint(
    output_path: str,
    all_records: dict,
    progress_path: str,
    completed_pas: set[str],
    total_pas: int,
    started_at: str,
) -> None:
    """Persist current records and progress to disk."""
    _atomic_write(output_path, all_records)
    _atomic_write(progress_path, {
        "completed_pas": sorted(completed_pas),
        "total_pas": total_pas,
        "started_at": started_at,
    })


async def run(practice_areas_path: str, force: bool = False) -> str:
    """Crawl listing pages for all practice areas and collect attorney records.

    Args:
        practice_areas_path: Path to practice_areas.json produced by the
            discover command. Must contain ``state_slug``, ``city_slug``,
            and ``practice_areas`` (list of slug strings).
        force: If True, ignore any existing checkpoint and re-crawl all
            practice areas from scratch.

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

    progress_path = os.path.join(data_dir, "crawl_progress.json")
    output_path = os.path.join(data_dir, "listings.json")

    # --- Resume or fresh start ---
    if force and os.path.exists(progress_path):
        os.remove(progress_path)
        log.info("--force: removed existing checkpoint, starting fresh")

    if not force and os.path.exists(progress_path):
        with open(progress_path, encoding="utf-8") as f:
            progress = json.load(f)
        completed_pas: set[str] = set(progress["completed_pas"])
        started_at: str = progress.get("started_at", datetime.now(timezone.utc).isoformat())
        with open(output_path, encoding="utf-8") as f:
            all_records: dict[str, dict] = json.load(f)
        remaining = [pa for pa in practice_areas if pa not in completed_pas]
        log.info(
            f"Resuming: {len(completed_pas)} PAs done, "
            f"{len(remaining)} remaining ({len(all_records)} attorneys loaded)"
        )
    else:
        all_records = {}
        completed_pas = set()
        remaining = list(practice_areas)
        started_at = datetime.now(timezone.utc).isoformat()

    referer = f"{config.BASE_URL}/{state_slug}/{city_slug}/"

    async with ScraperClient() as client:
        total_pas = len(practice_areas)

        for pa_slug in remaining:
            idx = practice_areas.index(pa_slug) + 1
            log.info(f"[{idx}/{total_pas}] Crawling: {pa_slug}")

            page = 1
            while page <= config.MAX_PAGES_PER_CATEGORY:
                url = (
                    f"{config.BASE_URL}/{pa_slug}/{state_slug}/{city_slug}/"
                    f"?page={page}"
                )
                html = await client.fetch(url, referer=referer)

                if html is None:
                    log.info(
                        f"  Page {page}: fetch returned None, stopping pagination"
                    )
                    break

                cards = parse_listing_page(html)

                if not cards:
                    log.info(
                        f"  Page {page}: 0 cards, stopping pagination"
                    )
                    break

                new_count = 0
                for record in cards:
                    if record.uuid not in all_records:
                        all_records[record.uuid] = record.to_dict()
                        new_count += 1

                log.info(
                    f"  Page {page}: {len(cards)} cards, {new_count} new "
                    f"(total unique: {len(all_records)})"
                )

                # Stop if no new unique attorneys were found on this page
                # (sponsored cards repeat on every page)
                if new_count == 0:
                    log.info(f"  No new attorneys on page {page}, stopping pagination")
                    break

                page += 1

            # Checkpoint after each PA
            completed_pas.add(pa_slug)
            _write_checkpoint(
                output_path, all_records, progress_path,
                completed_pas, total_pas, started_at,
            )

    # Final write
    _atomic_write(output_path, all_records)

    # Clean up progress file — signals "complete, no pending resume"
    if os.path.exists(progress_path):
        os.remove(progress_path)

    log.info(
        f"Listings complete: {len(all_records)} unique attorneys "
        f"written to {output_path}"
    )
    return output_path
