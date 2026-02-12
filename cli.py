#!/usr/bin/env python3
# cli.py
"""CLI entry point for the Super Lawyers scraping pipeline.

Provides argparse subcommands for all five pipeline phases:

    1. discover        - Resolve a location to practice area URLs
    2. crawl-listings  - Crawl listing pages and collect attorney records
    3. fetch-profiles  - Download raw profile HTML to disk
    4. parse-profiles  - Parse saved HTML into structured records
    5. export          - Clean records and export to CSV

Usage examples:
    python cli.py discover "Los Angeles, CA"
    python cli.py crawl-listings data/los-angeles_ca/practice_areas.json
    python cli.py fetch-profiles data/los-angeles_ca/listings.json
    python cli.py parse-profiles data/los-angeles_ca
    python cli.py export data/los-angeles_ca/records.json -o output/
    python cli.py -v discover "New York, NY"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def setup_logging(verbose: bool) -> None:
    """Configure root logger with timestamp, level, and module name.

    Args:
        verbose: If True, set level to DEBUG; otherwise INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )


# ---------------------------------------------------------------------------
# Subcommand handlers (lazy imports to keep startup fast)
# ---------------------------------------------------------------------------


def cmd_discover(args: argparse.Namespace) -> None:
    """Run the discover phase: resolve location to practice area URLs."""
    from commands import discover

    result = asyncio.run(discover.run(args.location))
    print(f"Output: {result}")


def cmd_crawl_listings(args: argparse.Namespace) -> None:
    """Run the crawl-listings phase: paginate listing pages for all practice areas."""
    from commands import crawl_listings

    result = asyncio.run(crawl_listings.run(args.input))
    print(f"Output: {result}")


def cmd_fetch_profiles(args: argparse.Namespace) -> None:
    """Run the fetch-profiles phase: download raw profile HTML."""
    from commands import fetch_profiles

    result = asyncio.run(fetch_profiles.run(args.input, force=args.force, retry_cf=args.retry_cf))
    print(f"Output: {result}")


def cmd_parse_profiles(args: argparse.Namespace) -> None:
    """Run the parse-profiles phase: parse HTML into structured records."""
    from commands import parse_profiles

    result = parse_profiles.run(args.data_dir)
    print(f"Output: {result}")


def cmd_export(args: argparse.Namespace) -> None:
    """Run the export phase: clean records and write CSV."""
    from commands import export

    result = export.run(args.input, args.output)
    print(f"Output: {result}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Super Lawyers scraping pipeline CLI",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose (DEBUG) logging",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- discover --
    sp_discover = subparsers.add_parser(
        "discover",
        help="Resolve a location to practice area URLs",
    )
    sp_discover.add_argument(
        "location",
        help='Location in "City, ST" format (e.g. "Los Angeles, CA")',
    )
    sp_discover.set_defaults(func=cmd_discover)

    # -- crawl-listings --
    sp_crawl = subparsers.add_parser(
        "crawl-listings",
        help="Crawl listing pages and collect attorney records",
    )
    sp_crawl.add_argument(
        "input",
        help="Path to practice_areas.json from the discover step",
    )
    sp_crawl.set_defaults(func=cmd_crawl_listings)

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
    sp_fetch.set_defaults(func=cmd_fetch_profiles)

    # -- parse-profiles --
    sp_parse = subparsers.add_parser(
        "parse-profiles",
        help="Parse saved HTML into structured records",
    )
    sp_parse.add_argument(
        "data_dir",
        help="Path to the data directory containing html/ and listings.json",
    )
    sp_parse.set_defaults(func=cmd_parse_profiles)

    # -- export --
    sp_export = subparsers.add_parser(
        "export",
        help="Clean records and export to CSV",
    )
    sp_export.add_argument(
        "input",
        help="Path to records.json from the parse-profiles step",
    )
    sp_export.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output directory for the CSV file (defaults to config.OUTPUT_DIR)",
    )
    sp_export.set_defaults(func=cmd_export)

    # -- parse and dispatch --
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
