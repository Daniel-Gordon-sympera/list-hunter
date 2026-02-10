"""Parse address blocks from Super Lawyers profile pages."""

import re

CSZ_PATTERN = re.compile(r"^(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$")


def parse_address(raw_text: str) -> dict:
    """Parse a raw address text block into street/city/state/zip components.

    Expects newline-separated lines, last line being "City, ST ZIP".
    Filters out "Office location" headings and "Phone:" lines.
    """
    lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]
    lines = [
        line for line in lines
        if not line.startswith("Phone:") and "Office location" not in line
    ]

    empty = {"street": "", "city": "", "state": "", "zip_code": ""}
    if not lines:
        return empty

    # Try last line as "City, ST ZIP"
    csz_match = CSZ_PATTERN.match(lines[-1])
    if csz_match:
        return {
            "street": ", ".join(lines[:-1]),
            "city": csz_match.group(1).strip(),
            "state": csz_match.group(2),
            "zip_code": csz_match.group(3),
        }

    # Try second-to-last line
    if len(lines) >= 2:
        csz_match = CSZ_PATTERN.match(lines[-2])
        if csz_match:
            return {
                "street": ", ".join(lines[:-2]),
                "city": csz_match.group(1).strip(),
                "state": csz_match.group(2),
                "zip_code": csz_match.group(3),
            }

    # Fallback: everything in street
    return {
        "street": ", ".join(lines),
        "city": "",
        "state": "",
        "zip_code": "",
    }
