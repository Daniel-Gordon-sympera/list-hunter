# commands/parse_profiles.py
"""Phase 4b: Parse saved HTML files into full AttorneyRecords."""

import json
import logging
import os
from dataclasses import fields
from datetime import datetime, timezone

from models import AttorneyRecord
from parsers.profile_parser import parse_profile
from http_client import is_cloudflare_challenge

log = logging.getLogger(__name__)


def merge_records(profile: AttorneyRecord, listing: AttorneyRecord) -> AttorneyRecord:
    """Merge profile data with listing pre-fill. Profile wins, listing fills gaps."""
    for f in fields(AttorneyRecord):
        profile_val = getattr(profile, f.name)
        listing_val = getattr(listing, f.name)
        if not profile_val and listing_val:
            setattr(profile, f.name, listing_val)
    return profile


def run(data_dir: str) -> str:
    """Parse all saved HTML files and merge with listing data. Returns path to records.json."""
    html_dir = os.path.join(data_dir, "html")
    listings_path = os.path.join(data_dir, "listings.json")

    # Load listing pre-fill data
    with open(listings_path, encoding="utf-8") as f:
        listings = json.load(f)

    # Find all HTML files
    html_files = [f for f in os.listdir(html_dir) if f.endswith(".html")]
    log.info(f"Parsing {len(html_files)} profile HTML files")

    records = []
    for i, filename in enumerate(html_files):
        uuid = filename.replace(".html", "")
        filepath = os.path.join(html_dir, filename)

        with open(filepath, encoding="utf-8") as f:
            html = f.read()

        # Get listing pre-fill data
        listing_data = listings.get(uuid, {})
        listing_record = AttorneyRecord(**{
            k: v for k, v in listing_data.items()
            if k in {f.name for f in fields(AttorneyRecord)}
        })

        # Skip Cloudflare challenge pages — use listing data only
        if is_cloudflare_challenge(html):
            log.warning("Skipping Cloudflare challenge HTML for %s", uuid)
            merged = listing_record
            merged.scraped_at = datetime.now(timezone.utc).isoformat()
            merged.profile_tier = merged.infer_profile_tier()
            records.append(merged)
            if (i + 1) % 100 == 0:
                log.info(f"  Parsed {i + 1}/{len(html_files)}")
            continue

        # Parse profile
        profile_url = listing_data.get("profile_url", "")
        profile_record = parse_profile(html, profile_url)

        # Merge
        merged = merge_records(profile_record, listing_record)
        merged.profile_tier = merged.infer_profile_tier()
        merged.scraped_at = datetime.now(timezone.utc).isoformat()

        records.append(merged)

        if (i + 1) % 100 == 0:
            log.info(f"  Parsed {i + 1}/{len(html_files)}")

    log.info(f"Parsing complete: {len(records)} records")

    # Write output
    output_path = os.path.join(data_dir, "records.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in records], f, indent=2, ensure_ascii=False)

    log.info(f"Wrote records to {output_path}")
    return output_path
