# commands/crawl_listings.py
"""Phase 3: Crawl listing pages, extract profile URLs and pre-fill data.

Reads practice_areas.json (from the discover command), paginates through
every practice-area listing page, parses attorney cards, deduplicates by
UUID, and writes listings.json to the data directory.
"""

import json
import logging
import os

import config
from http_client import ScraperClient
from parsers.listing_parser import parse_listing_page

log = logging.getLogger(__name__)


async def run(practice_areas_path: str) -> str:
    """Crawl listing pages for all practice areas and collect attorney records.

    Args:
        practice_areas_path: Path to practice_areas.json produced by the
            discover command. Must contain ``state_slug``, ``city_slug``,
            and ``practice_areas`` (list of slug strings).

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

    referer = f"{config.BASE_URL}/{state_slug}/{city_slug}/"

    # UUID -> record dict  (global dedup across all practice areas)
    all_records: dict[str, dict] = {}

    async with ScraperClient() as client:
        total_pas = len(practice_areas)

        for idx, pa_slug in enumerate(practice_areas, start=1):
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

                page += 1

    # Write output
    output_path = os.path.join(data_dir, "listings.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, indent=2, ensure_ascii=False)

    log.info(
        f"Listings complete: {len(all_records)} unique attorneys "
        f"written to {output_path}"
    )
    return output_path
