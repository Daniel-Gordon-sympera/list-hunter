#!/usr/bin/env python3
"""Run the full Super Lawyers scraping pipeline for a single location.

Usage:
    python main.py "Pasadena, CA"
    python main.py -v "Los Angeles, CA"
    python main.py "New York, NY" -o /tmp/output
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from log_setup import setup_logging

logger = logging.getLogger(__name__)


async def run_pipeline(
    location: str,
    output_dir: str | None = None,
    verbose: bool = False,
    workers: int | None = None,
    pa_filter: list[str] | None = None,
    max_results: int | None = None,
) -> str:
    """Chain all five scraper phases and return the path to the final CSV.

    Args:
        location: Location string in "City, ST" format.
        output_dir: Optional output directory for the CSV (defaults to config.OUTPUT_DIR).
        verbose: Whether to enable verbose logging.
        workers: Number of concurrent PA workers for crawl-listings.
        pa_filter: Optional list of PA slugs to crawl.
        max_results: Optional cap on unique attorneys to collect.

    Returns:
        Path to the exported CSV file.
    """
    from commands import crawl_listings, discover, export, fetch_profiles, parse_profiles

    # Phase 1+2: Discover practice areas
    logger.info("=== Phase 1+2: Discover ===")
    practice_areas_path = await discover.run(location)

    # Now that data_dir exists, add file logging
    data_dir = os.path.dirname(practice_areas_path)
    setup_logging(verbose=verbose, data_dir=data_dir, command_name="pipeline")

    # Phase 3: Crawl listing pages
    logger.info("=== Phase 3: Crawl listings ===")
    listings_path = await crawl_listings.run(
        practice_areas_path,
        workers=workers,
        pa_filter=pa_filter,
        max_results=max_results,
    )

    # Phase 4a: Fetch profile HTML
    logger.info("=== Phase 4a: Fetch profiles ===")
    data_dir = await fetch_profiles.run(listings_path)

    # Phase 4b: Parse profiles
    logger.info("=== Phase 4b: Parse profiles ===")
    records_path = parse_profiles.run(data_dir)

    # Phase 5: Export CSV
    logger.info("=== Phase 5: Export ===")
    csv_path = export.run(records_path, output_dir)

    return csv_path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Run the full Super Lawyers scraping pipeline for a location",
    )
    parser.add_argument(
        "location",
        help='Location in "City, ST" format (e.g. "Pasadena, CA")',
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output directory for the CSV file (defaults to config.OUTPUT_DIR)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Concurrent PA workers (default: 3)",
    )
    parser.add_argument(
        "--practice-areas",
        default=None,
        help="Comma-separated PA slugs to crawl (default: all)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Stop after N unique attorneys collected",
    )

    args = parser.parse_args()
    setup_logging(verbose=args.verbose, command_name="pipeline")

    pa_filter = (
        [s.strip() for s in args.practice_areas.split(",")]
        if args.practice_areas
        else None
    )

    csv_path = asyncio.run(
        run_pipeline(
            args.location,
            args.output,
            verbose=args.verbose,
            workers=args.workers,
            pa_filter=pa_filter,
            max_results=args.max_results,
        )
    )
    print(f"Done! CSV: {csv_path}")


if __name__ == "__main__":
    main()
