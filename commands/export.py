# commands/export.py
"""Phase 5: Clean records and export to CSV."""

import csv
import json
import logging
import os
import re
from dataclasses import fields
from datetime import datetime

from models import AttorneyRecord
import config

log = logging.getLogger(__name__)

TRACKING_PARAMS = re.compile(r"[?&](adSubId|fli|trk|utm_\w+)=[^&]*")

BOILERPLATE_PATTERNS = [
    re.compile(r"^[\w\s.]+ is an attorney who represents clients in the"),
    re.compile(r"Being selected to Super Lawyers is limited to a small number"),
    re.compile(r"passed the bar exam and was admitted to legal practice in"),
    re.compile(r"is recognized by peers and was selected to"),
]


def clean_phone(raw: str) -> str:
    """Strip +1 prefix and format 10-digit US numbers as XXX-XXX-XXXX."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return raw


def strip_tracking_params(url: str) -> str:
    """Remove adSubId, fli, trk, and utm_* query parameters from a URL."""
    cleaned = TRACKING_PARAMS.sub("", url)
    # Fix malformed query strings left behind after stripping
    cleaned = re.sub(r"\?&", "?", cleaned)
    cleaned = re.sub(r"\?$", "", cleaned)
    return cleaned


def clean_record(record: AttorneyRecord) -> AttorneyRecord:
    """Apply all cleaning rules to an AttorneyRecord in place and return it."""
    # Phone normalization
    if record.phone:
        record.phone = clean_phone(record.phone)

    # State uppercase
    if record.state:
        record.state = record.state.upper()

    # Strip tracking params from all URL fields
    for field_name in ("firm_website_url", "professional_webpage_url",
                       "linkedin_url", "facebook_url", "twitter_url",
                       "findlaw_url", "profile_url"):
        val = getattr(record, field_name)
        if val:
            setattr(record, field_name, strip_tracking_params(val))

    # Auto-generated bio detection and removal
    if record.about:
        for pattern in BOILERPLATE_PATTERNS:
            if pattern.search(record.about):
                record.about = ""
                break

    # Truncate any cell exceeding MAX_CELL_LENGTH
    for f in fields(record):
        val = getattr(record, f.name)
        if isinstance(val, str) and len(val) > config.MAX_CELL_LENGTH:
            setattr(record, f.name, val[:config.MAX_CELL_LENGTH] + "... [truncated]")

    return record


def run(records_path: str, output_dir: str | None = None) -> str:
    """Load records from JSON, clean them, and export to a timestamped CSV.

    Args:
        records_path: Path to a JSON file containing a list of attorney dicts.
        output_dir: Directory for the output CSV. Falls back to config.OUTPUT_DIR.

    Returns:
        The absolute path to the generated CSV file.
    """
    with open(records_path, encoding="utf-8") as f:
        raw_records = json.load(f)

    valid_fields = {f.name for f in fields(AttorneyRecord)}
    records = []
    for data in raw_records:
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        record = AttorneyRecord(**filtered)
        record = clean_record(record)
        records.append(record)

    if not output_dir:
        output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dirname = os.path.basename(os.path.dirname(records_path))
    filename = f"superlawyers_{dirname}_{timestamp}.csv"
    csv_path = os.path.join(output_dir, filename)

    with open(csv_path, "w", newline="", encoding=config.CSV_ENCODING) as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(AttorneyRecord.csv_headers())
        for record in records:
            writer.writerow(record.to_csv_row())

    log.info(f"CSV exported: {csv_path} ({len(records)} records)")
    return csv_path
