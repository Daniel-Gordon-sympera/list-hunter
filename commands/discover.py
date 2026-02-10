# commands/discover.py
"""Phase 1+2: Resolve a user location to URL slugs and discover practice areas.

Usage:
    python cli.py discover "Los Angeles, CA"

Produces:
    data/los-angeles_ca/practice_areas.json
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup
from slugify import slugify

from config import BASE_URL, DATA_DIR
from http_client import ScraperClient

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State mappings — all 50 states + DC
# ---------------------------------------------------------------------------

STATE_ABBREV_TO_SLUG: dict[str, str] = {
    "AL": "alabama",
    "AK": "alaska",
    "AZ": "arizona",
    "AR": "arkansas",
    "CA": "california",
    "CO": "colorado",
    "CT": "connecticut",
    "DE": "delaware",
    "DC": "washington-dc",
    "FL": "florida",
    "GA": "georgia",
    "HI": "hawaii",
    "ID": "idaho",
    "IL": "illinois",
    "IN": "indiana",
    "IA": "iowa",
    "KS": "kansas",
    "KY": "kentucky",
    "LA": "louisiana",
    "ME": "maine",
    "MD": "maryland",
    "MA": "massachusetts",
    "MI": "michigan",
    "MN": "minnesota",
    "MS": "mississippi",
    "MO": "missouri",
    "MT": "montana",
    "NE": "nebraska",
    "NV": "nevada",
    "NH": "new-hampshire",
    "NJ": "new-jersey",
    "NM": "new-mexico",
    "NY": "new-york",
    "NC": "north-carolina",
    "ND": "north-dakota",
    "OH": "ohio",
    "OK": "oklahoma",
    "OR": "oregon",
    "PA": "pennsylvania",
    "RI": "rhode-island",
    "SC": "south-carolina",
    "SD": "south-dakota",
    "TN": "tennessee",
    "TX": "texas",
    "UT": "utah",
    "VT": "vermont",
    "VA": "virginia",
    "WA": "washington",
    "WV": "west-virginia",
    "WI": "wisconsin",
    "WY": "wyoming",
}

STATE_FULL_TO_ABBREV: dict[str, str] = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "District of Columbia": "DC",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
}

SLUG_TO_ABBREV: dict[str, str] = {v: k for k, v in STATE_ABBREV_TO_SLUG.items()}

# Slugs that appear in the URL pattern but are not practice areas
_EXCLUDED_SLUGS = frozenset({"search", "advanced_search"})


# ---------------------------------------------------------------------------
# Phase 1: Location parsing
# ---------------------------------------------------------------------------


def parse_location(location_input: str) -> tuple[str, str]:
    """Parse a location string into (state_slug, city_slug).

    Accepts formats:
        "City, ST"          e.g. "Los Angeles, CA"
        "City, State Name"  e.g. "Los Angeles, California"

    Args:
        location_input: User-provided location string.

    Returns:
        Tuple of (state_slug, city_slug) for URL construction.

    Raises:
        ValueError: If the input cannot be parsed or the state is unrecognised.
    """
    if "," not in location_input:
        raise ValueError(
            f"Expected format 'City, ST' or 'City, State Name', "
            f"got: {location_input!r}"
        )

    parts = location_input.split(",", maxsplit=1)
    city_raw = parts[0].strip()
    state_raw = parts[1].strip()

    if not city_raw or not state_raw:
        raise ValueError(
            f"Expected format 'City, ST' or 'City, State Name', "
            f"got: {location_input!r}"
        )

    # Resolve state to abbreviation
    state_upper = state_raw.upper()
    if state_upper in STATE_ABBREV_TO_SLUG:
        state_abbrev = state_upper
    else:
        # Try full state name (title-case lookup)
        state_title = state_raw.title()
        if state_title in STATE_FULL_TO_ABBREV:
            state_abbrev = STATE_FULL_TO_ABBREV[state_title]
        else:
            raise ValueError(f"Unknown state: {state_raw!r}")

    state_slug = STATE_ABBREV_TO_SLUG[state_abbrev]
    city_slug = slugify(city_raw)

    return state_slug, city_slug


# ---------------------------------------------------------------------------
# Phase 2: Practice area discovery
# ---------------------------------------------------------------------------


async def discover_practice_areas(
    client: ScraperClient,
    state_slug: str,
    city_slug: str,
) -> list[str]:
    """Fetch the city index page and extract practice area slugs.

    Looks for links matching the pattern /{pa_slug}/{state_slug}/{city_slug}/
    and returns all unique practice area slugs, sorted alphabetically.

    Args:
        client: An active ScraperClient instance.
        state_slug: State URL slug (e.g. "california").
        city_slug: City URL slug (e.g. "los-angeles").

    Returns:
        Sorted list of practice area slug strings.

    Raises:
        RuntimeError: If the city page could not be fetched.
    """
    url = f"{BASE_URL}/{state_slug}/{city_slug}/"
    logger.info("Fetching city index: %s", url)

    html = await client.fetch(url)
    if html is None:
        raise RuntimeError(f"Failed to fetch city index page: {url}")

    soup = BeautifulSoup(html, "lxml")
    pattern = re.compile(
        rf"^/([^/]+)/{re.escape(state_slug)}/{re.escape(city_slug)}/$"
    )

    practice_areas: set[str] = set()
    for link in soup.find_all("a", href=True):
        match = pattern.match(link["href"])
        if match:
            slug = match.group(1)
            if slug not in _EXCLUDED_SLUGS and slug != state_slug:
                practice_areas.add(slug)

    result = sorted(practice_areas)
    logger.info("Found %d practice areas for %s/%s", len(result), state_slug, city_slug)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(location_input: str) -> str:
    """Main entry point for the discover command.

    Parses the location, fetches practice areas, and writes the result
    to a JSON file under DATA_DIR.

    Args:
        location_input: User-provided location string (e.g. "Los Angeles, CA").

    Returns:
        Path to the output practice_areas.json file.
    """
    state_slug, city_slug = parse_location(location_input)
    state_abbrev = SLUG_TO_ABBREV[state_slug].lower()

    # Create data directory: data/{city_slug}_{state_abbrev}/
    city_dir = os.path.join(DATA_DIR, f"{city_slug}_{state_abbrev}")
    os.makedirs(city_dir, exist_ok=True)
    logger.info("Data directory: %s", city_dir)

    # Fetch practice areas
    async with ScraperClient() as client:
        practice_areas = await discover_practice_areas(client, state_slug, city_slug)

    # Write output
    output_path = os.path.join(city_dir, "practice_areas.json")
    output_data = {
        "state_slug": state_slug,
        "city_slug": city_slug,
        "practice_areas": practice_areas,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info(
        "Wrote %d practice areas to %s", len(practice_areas), output_path
    )
    return output_path
