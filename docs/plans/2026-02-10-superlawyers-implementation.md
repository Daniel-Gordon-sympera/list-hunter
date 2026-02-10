# Super Lawyers Scraper Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a CLI scraper that accepts a city/state location and produces a 33-column CSV of attorney profiles from Super Lawyers.

**Architecture:** Five phases as separate CLI commands communicating through JSON/HTML files on disk. Parsers are pure functions tested against real HTML fixtures. HTTP client is a thin httpx wrapper with sleep-based rate limiting and tenacity retries.

**Tech Stack:** Python 3.11+, httpx, beautifulsoup4, lxml, tenacity, fake-useragent, python-slugify, pytest

**Design doc:** `docs/plans/2026-02-10-superlawyers-scraper-design.md`
**Original spec:** `superlawyers_scraping_plan.md` (use design doc as source of truth where they differ)

---

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `README.md`
- Create: `.gitignore`

**Step 1: Create requirements.txt**

```
httpx[http2]
beautifulsoup4
lxml
tenacity
fake-useragent
python-slugify
pytest
```

**Step 2: Create .gitignore**

```
__pycache__/
*.pyc
.pytest_cache/
data/
output/
*.egg-info/
.venv/
venv/
```

**Step 3: Create README.md**

```markdown
# Super Lawyers Scraper

Scrapes attorney profiles from the Super Lawyers directory.
Accepts a city/state location and produces a CSV with 33 data fields per attorney.

## Quick Start

### Prerequisites
- Python 3.11+

### Install

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### Scrape a city (full pipeline)

```bash
python cli.py discover "Los Angeles, CA"
python cli.py crawl-listings data/los-angeles_ca/practice_areas.json
python cli.py fetch-profiles data/los-angeles_ca/listings.json
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json
```

Output: `output/superlawyers_los-angeles_ca_YYYYMMDD_HHMMSS.csv`

## Commands

| Command | Input | Output |
|---------|-------|--------|
| discover | "City, ST" | practice_areas.json |
| crawl-listings | practice_areas.json | listings.json |
| fetch-profiles | listings.json | html/{uuid}.html files |
| parse-profiles | html/ dir + listings.json | records.json |
| export | records.json | .csv file |

## Re-parsing without re-fetching

If you need to fix a parser selector, you don't need to re-download anything:

```bash
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json
```

## Running tests

```bash
pytest tests/ -v
```
```

**Step 4: Create virtual environment and install deps**

Run: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

**Step 5: Create empty directory structure**

Run: `mkdir -p commands parsers tests/fixtures`

Create empty `__init__.py` in each package:
- Create: `commands/__init__.py` (empty)
- Create: `parsers/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)

**Step 6: Commit**

```bash
git add .gitignore requirements.txt README.md commands/__init__.py parsers/__init__.py tests/__init__.py
git commit -m "chore: project scaffolding with deps and README"
```

---

### Task 2: Config and Data Model

**Files:**
- Create: `config.py`
- Create: `models.py`
- Create: `tests/test_models.py`

**Step 1: Write tests for AttorneyRecord**

```python
# tests/test_models.py
from models import AttorneyRecord


def test_csv_headers_has_33_columns():
    headers = AttorneyRecord.csv_headers()
    assert len(headers) == 33
    assert headers[0] == "uuid"
    assert headers[-1] == "scraped_at"


def test_to_csv_row_matches_header_count():
    record = AttorneyRecord(uuid="abc-123", name="Jane Doe")
    row = record.to_csv_row()
    assert len(row) == 33
    assert row[0] == "abc-123"
    assert row[1] == "Jane Doe"


def test_completeness_score_empty():
    record = AttorneyRecord()
    # country="United States" and scraped_at are auto-set, so 2/33
    score = record.completeness_score()
    assert score == round(2 / 33, 2)


def test_completeness_score_partial():
    record = AttorneyRecord(uuid="x", name="Y", city="Z")
    # uuid, name, city, country, scraped_at = 5/33
    assert record.completeness_score() == round(5 / 33, 2)


def test_infer_tier_premium():
    record = AttorneyRecord(bar_activity="President, BHBA (2024)")
    assert record.infer_profile_tier() == "premium"


def test_infer_tier_expanded():
    record = AttorneyRecord(
        phone="310-271-0747",
        about="Attorney Jane Doe has practiced law for over 20 years specializing in corporate transactions and real estate matters across Southern California."
    )
    assert record.infer_profile_tier() == "expanded"


def test_infer_tier_basic():
    record = AttorneyRecord(name="John Smith")
    assert record.infer_profile_tier() == "basic"


def test_infer_tier_basic_with_auto_bio():
    record = AttorneyRecord(
        phone="555-1234",
        about="John Smith is an attorney who represents clients in the area of business law."
    )
    assert record.infer_profile_tier() == "basic"


def test_is_auto_bio_detects_boilerplate():
    record = AttorneyRecord(
        about="Being selected to Super Lawyers is limited to a small number of attorneys."
    )
    assert record._is_auto_bio() is True


def test_is_auto_bio_real_bio():
    record = AttorneyRecord(
        about="Jane has over 20 years of experience in corporate law and has handled over 500 mergers."
    )
    assert record._is_auto_bio() is False


def test_to_dict_returns_all_fields():
    record = AttorneyRecord(uuid="test-uuid")
    d = record.to_dict()
    assert d["uuid"] == "test-uuid"
    assert "scraped_at" in d
    assert len(d) == 33


def test_country_defaults_to_us():
    record = AttorneyRecord()
    assert record.country == "United States"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'models'`

**Step 3: Write config.py**

```python
# config.py
"""All configuration constants for the scraper."""

# URLs
BASE_URL = "https://attorneys.superlawyers.com"
PROFILE_BASE_URL = "https://profiles.superlawyers.com"

# Rate limiting
DELAY_MIN = 1.0                # seconds between requests
DELAY_MAX = 3.0
MAX_CONCURRENT = 5             # simultaneous profile fetches
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0      # exponential: 2s -> 4s -> 8s

# Pagination
MAX_PAGES_PER_CATEGORY = 200   # safety limit per practice area

# Output
OUTPUT_DIR = "./output"
DATA_DIR = "./data"
CSV_ENCODING = "utf-8-sig"
MULTIVALUE_DELIMITER = " ; "
MAX_CELL_LENGTH = 10_000

# Timeouts
REQUEST_TIMEOUT = 30           # seconds
```

**Step 4: Write models.py**

```python
# models.py
"""AttorneyRecord dataclass — 33 fields across 7 groups."""

from dataclasses import dataclass, field, fields, asdict
from datetime import datetime, timezone
import re


@dataclass
class AttorneyRecord:
    """Complete attorney data model — 33 fields across 7 groups."""

    # Group A: Identity
    uuid: str = ""
    name: str = ""
    firm_name: str = ""
    selection_type: str = ""
    selection_years: str = ""
    description: str = ""

    # Group B: Location
    street: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = "United States"
    geo_coordinates: str = ""

    # Group C: Contact
    phone: str = ""
    email: str = ""
    firm_website_url: str = ""
    professional_webpage_url: str = ""

    # Group D: Professional Profile
    about: str = ""
    practice_areas: str = ""
    focus_areas: str = ""
    licensed_since: str = ""
    education: str = ""
    languages: str = ""

    # Group E: Achievements
    honors: str = ""
    bar_activity: str = ""
    pro_bono: str = ""
    publications: str = ""

    # Group F: Social & Web
    linkedin_url: str = ""
    facebook_url: str = ""
    twitter_url: str = ""
    findlaw_url: str = ""

    # Group G: Metadata
    profile_url: str = ""
    profile_tier: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def csv_headers(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_csv_row(self) -> list[str]:
        return [getattr(self, f.name) for f in fields(self)]

    def to_dict(self) -> dict:
        return asdict(self)

    def completeness_score(self) -> float:
        filled = sum(1 for f in fields(self) if getattr(self, f.name))
        return round(filled / len(fields(self)), 2)

    def infer_profile_tier(self) -> str:
        if self.bar_activity or self.pro_bono or self.publications:
            return "premium"
        elif self.phone and self.about and not self._is_auto_bio():
            return "expanded"
        return "basic"

    def _is_auto_bio(self) -> bool:
        patterns = [
            r"^[\w\s.]+ is an attorney who represents clients in the",
            r"Being selected to Super Lawyers is limited to a small number",
            r"passed the bar exam and was admitted to legal practice in",
        ]
        return any(re.search(p, self.about) for p in patterns)
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: All 12 tests PASS

**Step 6: Commit**

```bash
git add config.py models.py tests/test_models.py
git commit -m "feat: add config constants and AttorneyRecord data model (33 fields)"
```

---

### Task 3: Validation Spike

**Files:**
- Create: `spike.py` (throwaway script)
- Output: `tests/fixtures/city_index.html`
- Output: `tests/fixtures/listing_page.html`
- Output: `tests/fixtures/profile_premium.html`

**Purpose:** Make 3 real HTTP requests to the live site to validate the plan's CSS selector assumptions. Save the HTML as test fixtures. This task produces the HTML files that all parser tests depend on.

**Step 1: Write the spike script**

```python
# spike.py
"""Validation spike: fetch 3 real pages, save as fixtures, report findings.

Run: python spike.py
Output: tests/fixtures/city_index.html, listing_page.html, profile_premium.html

This script is throwaway — it exists to validate selectors before building parsers.
"""

import httpx
import re
import os
import time

FIXTURES_DIR = "tests/fixtures"
os.makedirs(FIXTURES_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def fetch_and_save(url: str, filename: str, referer: str | None = None) -> str:
    headers = HEADERS.copy()
    if referer:
        headers["Referer"] = referer
    print(f"Fetching: {url}")
    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
    print(f"  Status: {resp.status_code}, Length: {len(resp.text)} chars")
    filepath = os.path.join(FIXTURES_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"  Saved: {filepath}")
    return resp.text


def main():
    # 1. City index page
    print("\n=== Step 1: City Index Page ===")
    city_html = fetch_and_save(
        "https://attorneys.superlawyers.com/california/los-angeles/",
        "city_index.html"
    )

    # Count practice area links
    pa_links = re.findall(r'href="/([\w-]+)/california/los-angeles/"', city_html)
    print(f"  Practice area links found: {len(set(pa_links))}")
    if pa_links:
        print(f"  First 5: {list(set(pa_links))[:5]}")

    time.sleep(2)

    # 2. Listing page
    print("\n=== Step 2: Listing Page ===")
    # Use first practice area found, or fallback
    pa_slug = pa_links[0] if pa_links else "business-and-corporate"
    listing_url = f"https://attorneys.superlawyers.com/{pa_slug}/california/los-angeles/"
    listing_html = fetch_and_save(
        listing_url,
        "listing_page.html",
        referer="https://attorneys.superlawyers.com/california/los-angeles/"
    )

    # Find profile URLs
    profile_urls = re.findall(
        r'href="(https://profiles\.superlawyers\.com/[^"]+\.html)',
        listing_html
    )
    unique_profiles = list(set(url.split("?")[0] for url in profile_urls))
    print(f"  Profile URLs found: {len(profile_urls)} (unique: {len(unique_profiles)})")
    if unique_profiles:
        print(f"  First: {unique_profiles[0]}")

    time.sleep(2)

    # 3. Profile page
    if unique_profiles:
        print("\n=== Step 3: Profile Page ===")
        profile_html = fetch_and_save(
            unique_profiles[0],
            "profile_premium.html",
            referer=listing_url
        )

        # Quick selector checks against plan assumptions
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(profile_html, "lxml")

        checks = {
            "h1 (name)": soup.select_one("h1"),
            "a[href*='/lawfirm/'] (firm)": soup.select_one("a[href*='/lawfirm/']"),
            "a[href^='tel:'] (phone)": soup.select_one("a[href^='tel:']"),
            "img[src*='maps.googleapis'] (geo)": soup.select_one("img[src*='maps.googleapis.com']"),
            "'Office location' text": soup.find(string=re.compile(r"Office location")),
            "a[href*='lawschools.superlawyers'] (edu)": soup.select_one("a[href*='lawschools.superlawyers.com']"),
            "'Selected to' text": soup.find(string=re.compile(r"Selected to")),
        }

        print("\n  Selector Validation:")
        for label, result in checks.items():
            status = "FOUND" if result else "MISSING"
            preview = ""
            if result:
                text = result.get_text(strip=True) if hasattr(result, "get_text") else str(result)[:80]
                preview = f" -> {text[:60]}"
            print(f"    {status}: {label}{preview}")
    else:
        print("\n  WARNING: No profile URLs found — listing page structure may differ from plan")

    print("\n=== Spike Complete ===")
    print(f"Fixtures saved to {FIXTURES_DIR}/")
    print("Next: inspect HTML files manually, then build parser tests against them.")


if __name__ == "__main__":
    main()
```

**Step 2: Run the spike**

Run: `python spike.py`

Expected output: Three HTML files saved to `tests/fixtures/`. Console output showing which selectors were found/missing. **Read the output carefully** — any "MISSING" selectors need investigation before proceeding to Task 4.

**Step 3: Review and document findings**

After running the spike:
- Open each HTML file and manually verify the plan's selector assumptions
- If selectors differ from the plan, note the actual selectors — they'll be used in Tasks 4-6
- If the site returns 403 or empty HTML, adjust headers and retry before proceeding

**Step 4: Commit the fixtures (not the spike)**

```bash
git add tests/fixtures/city_index.html tests/fixtures/listing_page.html tests/fixtures/profile_premium.html
git commit -m "test: add real HTML fixtures from validation spike"
```

> **STOP POINT:** Do not proceed to Task 4 until the spike HTML has been reviewed. The parser code in Tasks 4-6 must be written against the **actual** HTML structure, not the plan's assumptions. If selectors in the plan are wrong, adjust the code in Tasks 4-6 accordingly.

---

### Task 4: Address Parser

**Files:**
- Create: `parsers/address_parser.py`
- Create: `tests/test_address_parser.py`

**Step 1: Write failing tests**

```python
# tests/test_address_parser.py
from parsers.address_parser import parse_address


def test_standard_address():
    raw = "9777 Wilshire Blvd.\nSuite 517\nBeverly Hills, CA 90212"
    result = parse_address(raw)
    assert result["street"] == "9777 Wilshire Blvd., Suite 517"
    assert result["city"] == "Beverly Hills"
    assert result["state"] == "CA"
    assert result["zip_code"] == "90212"


def test_single_line_street():
    raw = "100 Main Street\nNew York, NY 10001"
    result = parse_address(raw)
    assert result["street"] == "100 Main Street"
    assert result["city"] == "New York"
    assert result["state"] == "NY"
    assert result["zip_code"] == "10001"


def test_zip_plus_four():
    raw = "500 Broadway\nSan Francisco, CA 94133-1234"
    result = parse_address(raw)
    assert result["zip_code"] == "94133-1234"


def test_strips_phone_and_heading():
    raw = "Office location for John Smith\n100 Main St\nPhone: 555-1234\nBoston, MA 02101"
    result = parse_address(raw)
    assert result["street"] == "100 Main St"
    assert result["city"] == "Boston"
    assert "Phone" not in result["street"]
    assert "Office location" not in result["street"]


def test_empty_input():
    result = parse_address("")
    assert result["street"] == ""
    assert result["city"] == ""
    assert result["state"] == ""
    assert result["zip_code"] == ""


def test_no_csz_match():
    raw = "Some Unknown Format\nNo City State Zip Here"
    result = parse_address(raw)
    assert result["street"] == "Some Unknown Format, No City State Zip Here"
    assert result["city"] == ""
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_address_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write implementation**

```python
# parsers/address_parser.py
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
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_address_parser.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add parsers/address_parser.py tests/test_address_parser.py
git commit -m "feat: address parser with city/state/zip extraction"
```

---

### Task 5: Listing Parser

**Files:**
- Create: `parsers/listing_parser.py`
- Create: `tests/test_listing_parser.py`
- Read: `tests/fixtures/listing_page.html` (from spike)

**Step 1: Examine the listing fixture**

Before writing tests, read `tests/fixtures/listing_page.html` and identify the actual HTML structure of attorney cards. Find:
- The container element for each attorney card
- Where the profile URL with UUID lives
- Where name, firm, phone, description, selection type appear

Document the actual selectors found — they may differ from the plan's assumptions.

**Step 2: Write failing tests**

Write tests based on what the spike HTML **actually** contains. The test below is a template — adjust field values to match real data from the fixture.

```python
# tests/test_listing_parser.py
import os
from parsers.listing_parser import parse_listing_page

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_parse_listing_page_returns_records():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    assert len(records) > 0, "Should find at least one attorney card"


def test_listing_records_have_uuid():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.uuid, f"Record missing UUID: {r.name}"
        assert len(r.uuid) == 36, f"UUID wrong length: {r.uuid}"


def test_listing_records_have_profile_url():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.profile_url.startswith("https://profiles.superlawyers.com/")
        assert "?" not in r.profile_url, "Tracking params should be stripped"


def test_listing_records_have_name():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.name, f"Record missing name for UUID {r.uuid}"


def test_listing_records_have_selection_type():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.selection_type in ("Super Lawyers", "Rising Stars", ""), \
            f"Unexpected selection_type: {r.selection_type}"


def test_empty_html_returns_empty():
    records = parse_listing_page("<html><body></body></html>")
    assert records == []
```

**Step 3: Run tests to verify they fail**

Run: `pytest tests/test_listing_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 4: Write implementation**

Write the parser based on the **actual** HTML structure observed in the fixture. The code below follows the plan's selectors — adjust if the spike revealed differences.

```python
# parsers/listing_parser.py
"""Parse attorney listing pages into partial AttorneyRecord objects."""

import re
from bs4 import BeautifulSoup
from models import AttorneyRecord

UUID_PATTERN = re.compile(r"/([\w-]{36})\.html")
PROFILE_URL_PATTERN = re.compile(r"https://profiles\.superlawyers\.com/.+\.html")


def parse_listing_page(html: str) -> list[AttorneyRecord]:
    """Extract partial AttorneyRecords from a listing page.

    Each record has up to 7 fields pre-filled:
    uuid, name, firm_name, phone, description, selection_type, profile_url
    """
    soup = BeautifulSoup(html, "lxml")
    records = []
    seen_uuids = set()

    for link in soup.find_all("a", href=PROFILE_URL_PATTERN):
        href = link["href"]
        uuid_match = UUID_PATTERN.search(href)
        if not uuid_match:
            continue

        uuid = uuid_match.group(1)
        if uuid in seen_uuids:
            continue
        seen_uuids.add(uuid)

        clean_url = href.split("?")[0]
        record = AttorneyRecord(uuid=uuid, profile_url=clean_url)

        # Walk up to the card container to find sibling data
        card = link.find_parent("div")
        if not card:
            records.append(record)
            continue

        # Name: h2 text
        name_el = card.select_one("h2")
        if name_el:
            record.name = name_el.get_text(strip=True)

        # Firm name
        firm_el = card.select_one('a[href*="/lawfirm/"]')
        if firm_el:
            record.firm_name = firm_el.get_text(strip=True)

        # Phone
        phone_el = card.select_one('a[href^="tel:"]')
        if phone_el:
            raw = phone_el["href"].replace("tel:", "").replace("+1", "")
            record.phone = raw.lstrip("1") if raw.startswith("1") else raw

        # Description (tagline paragraph)
        desc_el = card.select_one("p")
        if desc_el:
            record.description = desc_el.get_text(strip=True)

        # Selection type
        card_text = card.get_text()
        if "Rising Stars" in card_text:
            record.selection_type = "Rising Stars"
        elif "Super Lawyers" in card_text:
            record.selection_type = "Super Lawyers"

        records.append(record)

    return records
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_listing_parser.py -v`
Expected: All 6 tests PASS

If tests fail because the actual HTML structure differs from the plan, adjust the selectors in the parser to match reality. This is expected — it's why we did the spike first.

**Step 6: Commit**

```bash
git add parsers/listing_parser.py tests/test_listing_parser.py
git commit -m "feat: listing page parser with 7-field pre-fill extraction"
```

---

### Task 6: Profile Parser

**Files:**
- Create: `parsers/profile_parser.py`
- Create: `tests/test_profile_parser.py`
- Read: `tests/fixtures/profile_premium.html` (from spike)

This is the largest and most important task. The profile parser extracts all 33 fields.

**Step 1: Examine the profile fixture**

Read `tests/fixtures/profile_premium.html` carefully. For each of the 33 fields, identify the actual CSS selector or regex that extracts it. Note any differences from the plan's Appendix A selectors.

**Step 2: Write failing tests**

Write tests based on the **actual** fixture content. Replace placeholder values below with real data from the fixture.

```python
# tests/test_profile_parser.py
import os
from parsers.profile_parser import parse_profile

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


class TestProfileParserPremium:
    """Tests against the premium profile fixture from the validation spike."""

    def setup_method(self):
        html = _load_fixture("profile_premium.html")
        # Replace with the actual URL from the fixture
        self.record = parse_profile(
            html, "https://profiles.superlawyers.com/california/los-angeles/lawyer/example/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html"
        )

    def test_uuid_extracted(self):
        assert self.record.uuid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_name_not_empty(self):
        assert self.record.name

    def test_profile_url_set(self):
        assert self.record.profile_url.startswith("https://profiles.superlawyers.com/")
        assert "?" not in self.record.profile_url

    def test_country_is_us(self):
        assert self.record.country == "United States"

    # Add assertions for every non-empty field in the fixture:
    # test_firm_name, test_phone, test_street, test_city, test_state,
    # test_zip_code, test_selection_type, test_practice_areas, etc.
    # Use the ACTUAL values from the saved HTML fixture.


class TestProfileParserEmpty:
    """Edge case: minimal/empty HTML."""

    def test_empty_html_returns_record(self):
        record = parse_profile("<html><body></body></html>", "https://example.com/x/00000000-0000-0000-0000-000000000000.html")
        assert record.uuid == "00000000-0000-0000-0000-000000000000"
        assert record.name == ""
        assert record.country == "United States"


class TestSafeExtraction:
    """Verify that one broken field doesn't kill the whole record."""

    def test_survives_malformed_html(self):
        html = "<html><body><h1>Test Name</h1><div>broken<</div></body></html>"
        record = parse_profile(html, "https://example.com/x/11111111-2222-3333-4444-555555555555.html")
        assert record.name == "Test Name"
        assert record.uuid == "11111111-2222-3333-4444-555555555555"
```

> **Important:** After running the spike (Task 3), replace placeholder UUIDs and field values with actual data from `tests/fixtures/profile_premium.html`. The `TestProfileParserPremium` class must assert against real extracted values.

**Step 3: Run tests to verify they fail**

Run: `pytest tests/test_profile_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 4: Write implementation**

```python
# parsers/profile_parser.py
"""Parse individual attorney profile pages into full AttorneyRecord objects."""

import re
import logging
from bs4 import BeautifulSoup
from models import AttorneyRecord
from parsers.address_parser import parse_address
import config

log = logging.getLogger(__name__)

UUID_PATTERN = re.compile(r"/([\w-]{36})\.html")


def parse_profile(html: str, url: str) -> AttorneyRecord:
    """Parse a profile page HTML into an AttorneyRecord with all 33 fields."""
    parser = _ProfileParser(html, url)
    return parser.parse()


class _ProfileParser:
    def __init__(self, html: str, url: str):
        self.soup = BeautifulSoup(html, "lxml")
        self.url = url
        self.text = self.soup.get_text()

    def parse(self) -> AttorneyRecord:
        r = AttorneyRecord()

        # Group A: Identity
        r.uuid = self._extract_uuid()
        r.name = self._safe(self._extract_name)
        r.firm_name = self._safe(self._extract_firm_name)
        r.selection_type = self._safe(self._extract_selection_type)
        r.selection_years = self._safe(self._extract_selection_years)

        # Group B: Location
        addr = self._safe(self._extract_address, default={})
        r.street = addr.get("street", "")
        r.city = addr.get("city", "")
        r.state = addr.get("state", "")
        r.zip_code = addr.get("zip_code", "")
        r.country = "United States"
        r.geo_coordinates = self._safe(self._extract_geo_coordinates)

        # Group C: Contact
        r.phone = self._safe(self._extract_phone)
        r.email = self._safe(self._extract_email)
        r.firm_website_url = self._safe(self._extract_firm_website)
        r.professional_webpage_url = self._safe(self._extract_professional_webpage)

        # Group D: Professional
        r.about = self._safe(self._extract_about)
        r.practice_areas = self._safe(self._extract_practice_areas)
        r.focus_areas = self._safe(self._extract_focus_areas)
        r.licensed_since = self._safe(self._extract_licensed_since)
        r.education = self._safe(self._extract_education)
        r.languages = self._safe(self._extract_languages)

        # Group E: Achievements
        r.honors = self._safe(self._extract_section, "Honors")
        r.bar_activity = self._safe(self._extract_section, "Bar / Professional Activity")
        r.pro_bono = self._safe(self._extract_section, "Pro bono / Community Service")
        r.publications = self._safe(self._extract_section, "Scholarly Lectures / Writings")

        # Group F: Social
        socials = self._safe(self._extract_social_links, default={})
        r.linkedin_url = socials.get("linkedin", "")
        r.facebook_url = socials.get("facebook", "")
        r.twitter_url = socials.get("twitter", "")
        r.findlaw_url = socials.get("findlaw", "")

        # Group G: Metadata
        r.profile_url = self.url.split("?")[0]

        return r

    def _extract_uuid(self) -> str:
        match = UUID_PATTERN.search(self.url)
        return match.group(1) if match else ""

    def _extract_name(self) -> str:
        h1 = self.soup.select_one("h1")
        return h1.get_text(strip=True) if h1 else ""

    def _extract_firm_name(self) -> str:
        firm_link = self.soup.select_one('a[href*="/lawfirm/"]')
        return firm_link.get_text(strip=True) if firm_link else ""

    def _extract_phone(self) -> str:
        tel = self.soup.select_one('a[href^="tel:"]')
        if tel:
            raw = tel["href"].replace("tel:", "").replace("+1", "")
            return raw.lstrip("1") if raw.startswith("1") else raw
        return ""

    def _extract_email(self) -> str:
        mailto = self.soup.select_one('a[href^="mailto:"]')
        if mailto:
            return mailto["href"].replace("mailto:", "")
        return ""

    def _extract_address(self) -> dict:
        heading = self.soup.find(string=re.compile(r"Office location for"))
        if not heading:
            return {}
        parent = heading.find_parent()
        if not parent:
            return {}
        addr_text = parent.get_text(separator="\n", strip=True)
        return parse_address(addr_text)

    def _extract_geo_coordinates(self) -> str:
        maps_img = self.soup.select_one('img[src*="maps.googleapis.com"]')
        if maps_img:
            match = re.search(r"center=([-\d.]+),([-\d.]+)", maps_img["src"])
            if match:
                return f"{match.group(1)},{match.group(2)}"
        maps_link = self.soup.select_one('a[href*="google.com/maps"]')
        if maps_link:
            match = re.search(r"@([-\d.]+),([-\d.]+)", maps_link["href"])
            if match:
                return f"{match.group(1)},{match.group(2)}"
        return ""

    def _extract_about(self) -> str:
        about_parts = []
        in_about = False
        for el in self.soup.find_all(["p", "h3", "h2"]):
            if el.name in ("h3", "h2"):
                text = el.get_text(strip=True)
                if "About" in text:
                    in_about = True
                    continue
                elif in_about:
                    break
            elif in_about and el.name == "p":
                about_parts.append(el.get_text(strip=True))
        return "\n\n".join(about_parts)

    def _extract_practice_areas(self) -> str:
        h3 = self.soup.find("h3", string=re.compile(r"^Practice areas?$", re.I))
        if h3:
            next_el = h3.find_next_sibling()
            if next_el:
                return next_el.get_text(strip=True)
        return ""

    def _extract_focus_areas(self) -> str:
        h3 = self.soup.find("h3", string=re.compile(r"^Focus areas?$", re.I))
        if h3:
            next_el = h3.find_next_sibling()
            if next_el:
                items = next_el.get_text(strip=True)
                return items.replace(", ", config.MULTIVALUE_DELIMITER)
        return ""

    def _extract_licensed_since(self) -> str:
        match = re.search(
            r"(?:Licensed in|First Admitted:)\s*(.+?)(?:\n|$)", self.text
        )
        return match.group(1).strip() if match else ""

    def _extract_education(self) -> str:
        edu_link = self.soup.select_one('a[href*="lawschools.superlawyers.com"]')
        if edu_link:
            return edu_link.get_text(strip=True)
        return ""

    def _extract_languages(self) -> str:
        match = re.search(r"Languages?\s+spoken:\s*(.+?)(?:\n|$)", self.text)
        if match:
            return match.group(1).strip().replace(", ", config.MULTIVALUE_DELIMITER)
        return ""

    def _extract_selection_type(self) -> str:
        if "Selected to Rising Stars" in self.text:
            return "Rising Stars"
        elif "Selected to Super Lawyers" in self.text:
            return "Super Lawyers"
        return ""

    def _extract_selection_years(self) -> str:
        match = re.search(
            r"Selected to (?:Super Lawyers|Rising Stars):\s*(\d{4}\s*-\s*\d{4})",
            self.text,
        )
        return match.group(1).strip() if match else ""

    def _extract_firm_website(self) -> str:
        visit = self.soup.find("a", string=re.compile(r"Visit website", re.I))
        if visit and visit.get("href"):
            return visit["href"].split("?")[0]
        return ""

    def _extract_professional_webpage(self) -> str:
        match = re.search(r"Professional Webpage:\s*(https?://\S+)", self.text)
        return match.group(1).strip() if match else ""

    def _extract_social_links(self) -> dict:
        links = {}
        section = self.soup.find(string=re.compile(r"Find me online"))
        scope = section.find_parent() if section else self.soup
        for a in scope.find_all("a", href=True):
            href = a["href"]
            hl = href.lower()
            if "linkedin.com" in hl and "linkedin" not in links:
                links["linkedin"] = href
            elif "facebook.com" in hl and "facebook" not in links:
                links["facebook"] = href
            elif ("twitter.com" in hl or "x.com" in hl) and "twitter" not in links:
                links["twitter"] = href
            elif "findlaw.com" in hl and "findlaw" not in links:
                links["findlaw"] = href
        return links

    def _extract_section(self, heading_text: str) -> str:
        h3 = self.soup.find("h3", string=re.compile(re.escape(heading_text)))
        if not h3:
            return ""
        items = []
        for sib in h3.find_next_siblings():
            if sib.name in ("h3", "hr"):
                break
            if sib.name == "ul":
                for li in sib.find_all("li"):
                    items.append(li.get_text(strip=True))
            elif sib.name == "li":
                items.append(sib.get_text(strip=True))
        return config.MULTIVALUE_DELIMITER.join(items)

    def _safe(self, func, *args, default=""):
        try:
            result = func(*args) if args else func()
            return result if result else default
        except Exception as e:
            log.warning(f"Extraction error in {func.__name__}: {e}")
            return default
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_profile_parser.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add parsers/profile_parser.py tests/test_profile_parser.py
git commit -m "feat: profile parser with 33-field extraction and safe wrappers"
```

---

### Task 7: HTTP Client

**Files:**
- Create: `http_client.py`

**Step 1: Write implementation**

No TDD for this module — it wraps network I/O and is best tested via integration (the commands that use it). Keep it thin.

```python
# http_client.py
"""Thin async HTTP client with rate limiting, retries, and header rotation."""

import asyncio
import random
import logging
from typing import Optional

import httpx
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import config

log = logging.getLogger(__name__)
ua = UserAgent()

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


class ScraperClient:
    """Async HTTP client with polite rate limiting and automatic retries."""

    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=config.REQUEST_TIMEOUT,
            follow_redirects=True,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    async def fetch(self, url: str, referer: str | None = None) -> Optional[str]:
        """Fetch a URL with rate limiting. Returns HTML string or None on failure."""
        delay = random.uniform(config.DELAY_MIN, config.DELAY_MAX)
        await asyncio.sleep(delay)

        headers = BASE_HEADERS.copy()
        headers["User-Agent"] = ua.random
        if referer:
            headers["Referer"] = referer

        try:
            return await self._fetch_with_retry(url, headers)
        except Exception as e:
            log.error(f"Failed after retries: {url} — {e}")
            return None

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=config.RETRY_BACKOFF_BASE, min=2, max=60),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        reraise=True,
    )
    async def _fetch_with_retry(self, url: str, headers: dict) -> str:
        resp = await self._client.get(url, headers=headers)

        if resp.status_code == 404:
            log.warning(f"404: {url}")
            return None

        if resp.status_code == 403:
            headers["User-Agent"] = ua.random
            log.warning(f"403: {url} — rotating UA and retrying")

        resp.raise_for_status()
        return resp.text
```

**Step 2: Commit**

```bash
git add http_client.py
git commit -m "feat: async HTTP client with rate limiting and tenacity retries"
```

---

### Task 8: Discover Command

**Files:**
- Create: `commands/discover.py`

**Step 1: Write implementation**

```python
# commands/discover.py
"""Phase 1+2: Resolve location to URL slugs, discover practice areas."""

import asyncio
import json
import logging
import os
import re

from bs4 import BeautifulSoup
from slugify import slugify

import config
from http_client import ScraperClient

log = logging.getLogger(__name__)

STATE_ABBREV_TO_SLUG = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new-hampshire", "NJ": "new-jersey", "NM": "new-mexico", "NY": "new-york",
    "NC": "north-carolina", "ND": "north-dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode-island", "SC": "south-carolina",
    "SD": "south-dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "DC": "washington-dc",
    "WV": "west-virginia", "WI": "wisconsin", "WY": "wyoming",
}

SLUG_TO_ABBREV = {v: k for k, v in STATE_ABBREV_TO_SLUG.items()}

# Also support full state names
STATE_FULL_TO_ABBREV = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "district of columbia": "DC", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY",
}


def parse_location(location_input: str) -> tuple[str, str]:
    """Parse 'City, ST' or 'City, State Name' into (state_slug, city_slug).

    Raises ValueError if format is invalid or state is unrecognized.
    """
    parts = [p.strip() for p in location_input.strip().split(",")]
    if len(parts) != 2:
        raise ValueError(f"Expected 'City, State' format, got: {location_input}")

    city, state_raw = parts
    state_raw = state_raw.strip()

    # Resolve state to abbreviation
    if len(state_raw) == 2:
        state_abbrev = state_raw.upper()
    else:
        state_abbrev = STATE_FULL_TO_ABBREV.get(state_raw.lower())
        if not state_abbrev:
            raise ValueError(f"Unrecognized state: {state_raw}")

    if state_abbrev not in STATE_ABBREV_TO_SLUG:
        raise ValueError(f"Unknown state abbreviation: {state_abbrev}")

    state_slug = STATE_ABBREV_TO_SLUG[state_abbrev]
    city_slug = slugify(city)

    return state_slug, city_slug


async def discover_practice_areas(
    client: ScraperClient, state_slug: str, city_slug: str
) -> list[str]:
    """Fetch the city page and extract all practice area slugs."""
    url = f"{config.BASE_URL}/{state_slug}/{city_slug}/"
    html = await client.fetch(url)
    if not html:
        raise RuntimeError(f"Failed to fetch city page: {url}")

    soup = BeautifulSoup(html, "lxml")
    practice_areas = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.match(
            rf"^/([\w-]+)/{re.escape(state_slug)}/{re.escape(city_slug)}/?$",
            href,
        )
        if match:
            pa_slug = match.group(1)
            if pa_slug not in ("search", "advanced_search", state_slug):
                practice_areas.add(pa_slug)

    log.info(f"Discovered {len(practice_areas)} practice areas for {state_slug}/{city_slug}")
    return sorted(practice_areas)


async def run(location_input: str) -> str:
    """Run discover command. Returns path to output JSON file."""
    state_slug, city_slug = parse_location(location_input)
    log.info(f"Resolved location: {state_slug}/{city_slug}")

    # Create data directory
    data_dir = os.path.join(config.DATA_DIR, f"{city_slug}_{SLUG_TO_ABBREV.get(state_slug, state_slug).lower()}")
    os.makedirs(data_dir, exist_ok=True)

    async with ScraperClient() as client:
        # Validate city page exists
        practice_areas = await discover_practice_areas(client, state_slug, city_slug)

    if not practice_areas:
        raise RuntimeError(f"No practice areas found for {state_slug}/{city_slug}")

    # Write output
    output_path = os.path.join(data_dir, "practice_areas.json")
    output = {
        "state_slug": state_slug,
        "city_slug": city_slug,
        "practice_areas": practice_areas,
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    log.info(f"Wrote {len(practice_areas)} practice areas to {output_path}")
    return output_path
```

**Step 2: Commit**

```bash
git add commands/discover.py
git commit -m "feat: discover command — location resolution + practice area extraction"
```

---

### Task 9: Crawl Listings Command

**Files:**
- Create: `commands/crawl_listings.py`

**Step 1: Write implementation**

```python
# commands/crawl_listings.py
"""Phase 3: Crawl listing pages, extract profile URLs + pre-fill data."""

import asyncio
import json
import logging
import os

import config
from http_client import ScraperClient
from parsers.listing_parser import parse_listing_page

log = logging.getLogger(__name__)


async def run(practice_areas_path: str) -> str:
    """Crawl all listing pages for a city. Returns path to listings.json."""
    with open(practice_areas_path) as f:
        data = json.load(f)

    state_slug = data["state_slug"]
    city_slug = data["city_slug"]
    practice_areas = data["practice_areas"]
    data_dir = os.path.dirname(practice_areas_path)

    results = {}  # uuid -> partial record dict

    async with ScraperClient() as client:
        for i, pa in enumerate(practice_areas):
            log.info(f"[{i + 1}/{len(practice_areas)}] Crawling: {pa}")
            page = 1

            while page <= config.MAX_PAGES_PER_CATEGORY:
                url = f"{config.BASE_URL}/{pa}/{state_slug}/{city_slug}/?page={page}"
                referer = f"{config.BASE_URL}/{state_slug}/{city_slug}/"

                html = await client.fetch(url, referer=referer)
                if not html:
                    break

                cards = parse_listing_page(html)
                if not cards:
                    break

                new_count = 0
                for card in cards:
                    if card.uuid and card.uuid not in results:
                        results[card.uuid] = card.to_dict()
                        new_count += 1

                log.info(
                    f"  Page {page}: {len(cards)} cards, "
                    f"{new_count} new (total unique: {len(results)})"
                )

                page += 1

    # Write output
    output_path = os.path.join(data_dir, "listings.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    log.info(f"Listing crawl complete: {len(results)} unique attorneys -> {output_path}")
    return output_path
```

**Step 2: Commit**

```bash
git add commands/crawl_listings.py
git commit -m "feat: crawl-listings command — paginated listing crawl with UUID dedup"
```

---

### Task 10: Fetch Profiles Command

**Files:**
- Create: `commands/fetch_profiles.py`

**Step 1: Write implementation**

```python
# commands/fetch_profiles.py
"""Phase 4a: Download raw profile HTML to disk."""

import asyncio
import json
import logging
import os

import config
from http_client import ScraperClient

log = logging.getLogger(__name__)


async def run(listings_path: str) -> str:
    """Fetch all profile pages and save raw HTML. Returns path to data dir."""
    with open(listings_path) as f:
        listings = json.load(f)

    data_dir = os.path.dirname(listings_path)
    html_dir = os.path.join(data_dir, "html")
    os.makedirs(html_dir, exist_ok=True)

    # Filter to profiles not yet fetched
    to_fetch = {}
    for uuid, record in listings.items():
        html_path = os.path.join(html_dir, f"{uuid}.html")
        if not os.path.exists(html_path):
            to_fetch[uuid] = record

    log.info(
        f"Profiles to fetch: {len(to_fetch)} "
        f"(skipping {len(listings) - len(to_fetch)} already on disk)"
    )

    status = {}  # uuid -> "success" | "failed" | "skipped"

    # Mark already-fetched as skipped
    for uuid in listings:
        if uuid not in to_fetch:
            status[uuid] = "skipped"

    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)
    total = len(to_fetch)

    async def fetch_one(client: ScraperClient, uuid: str, record: dict, index: int):
        async with semaphore:
            profile_url = record.get("profile_url", "")
            if not profile_url:
                log.warning(f"No profile URL for {uuid}")
                status[uuid] = "failed"
                return

            name = record.get("name", uuid)
            log.info(f"[{index + 1}/{total}] Fetching: {name}")

            # Use listing page as referer
            referer = config.BASE_URL
            html = await client.fetch(profile_url, referer=referer)

            if html:
                html_path = os.path.join(html_dir, f"{uuid}.html")
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(html)
                status[uuid] = "success"
            else:
                log.warning(f"Failed to fetch profile: {name} ({uuid})")
                status[uuid] = "failed"

    async with ScraperClient() as client:
        tasks = [
            fetch_one(client, uuid, record, i)
            for i, (uuid, record) in enumerate(to_fetch.items())
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Write status
    status_path = os.path.join(data_dir, "fetch_status.json")
    with open(status_path, "w") as f:
        json.dump(status, f, indent=2)

    success = sum(1 for v in status.values() if v == "success")
    failed = sum(1 for v in status.values() if v == "failed")
    skipped = sum(1 for v in status.values() if v == "skipped")
    log.info(f"Fetch complete: {success} success, {failed} failed, {skipped} skipped")

    return data_dir
```

**Step 2: Commit**

```bash
git add commands/fetch_profiles.py
git commit -m "feat: fetch-profiles command — idempotent HTML download with concurrency"
```

---

### Task 11: Parse Profiles Command

**Files:**
- Create: `commands/parse_profiles.py`

**Step 1: Write implementation**

```python
# commands/parse_profiles.py
"""Phase 4b: Parse saved HTML files into full AttorneyRecords."""

import json
import logging
import os
from dataclasses import fields
from datetime import datetime, timezone

from models import AttorneyRecord
from parsers.profile_parser import parse_profile

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
    with open(listings_path) as f:
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
```

**Step 2: Commit**

```bash
git add commands/parse_profiles.py
git commit -m "feat: parse-profiles command — HTML-to-record parsing with listing merge"
```

---

### Task 12: Export Command

**Files:**
- Create: `commands/export.py`
- Create: `tests/test_export.py`

**Step 1: Write failing tests**

```python
# tests/test_export.py
import os
import csv
import json
import tempfile
from commands.export import clean_record, run
from models import AttorneyRecord


def test_clean_phone_formats():
    record = AttorneyRecord(phone="+13102710747")
    cleaned = clean_record(record)
    assert cleaned.phone == "310-271-0747"


def test_clean_phone_already_formatted():
    record = AttorneyRecord(phone="310-271-0747")
    cleaned = clean_record(record)
    assert cleaned.phone == "310-271-0747"


def test_clean_strips_url_tracking():
    record = AttorneyRecord(
        firm_website_url="https://example.com?adSubId=123&fli=456",
        linkedin_url="https://linkedin.com/in/test?trk=abc",
    )
    cleaned = clean_record(record)
    assert cleaned.firm_website_url == "https://example.com"
    assert "trk" not in cleaned.linkedin_url


def test_clean_auto_bio_removed():
    record = AttorneyRecord(
        about="John Smith is an attorney who represents clients in the area of business law."
    )
    cleaned = clean_record(record)
    assert cleaned.about == ""


def test_clean_real_bio_kept():
    bio = "Jane has over 20 years of experience in corporate law."
    record = AttorneyRecord(about=bio)
    cleaned = clean_record(record)
    assert cleaned.about == bio


def test_clean_state_uppercase():
    record = AttorneyRecord(state="ca")
    cleaned = clean_record(record)
    assert cleaned.state == "CA"


def test_clean_truncates_long_cell():
    record = AttorneyRecord(about="x" * 15_000)
    cleaned = clean_record(record)
    assert len(cleaned.about) <= 10_013  # 10000 + "... [truncated]"
    assert cleaned.about.endswith("... [truncated]")


def test_export_produces_csv(tmp_path):
    records_data = [
        AttorneyRecord(uuid="test-1", name="Test Attorney", city="LA", state="CA").to_dict()
    ]
    records_path = tmp_path / "records.json"
    with open(records_path, "w") as f:
        json.dump(records_data, f)

    output_dir = tmp_path / "output"
    csv_path = run(str(records_path), str(output_dir))

    assert os.path.exists(csv_path)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        assert len(headers) == 33
        row = next(reader)
        assert row[0] == "test-1"
        assert row[1] == "Test Attorney"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write implementation**

```python
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
    """Normalize phone to XXX-XXX-XXXX format."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return raw


def strip_tracking_params(url: str) -> str:
    """Remove known tracking query params from a URL."""
    cleaned = TRACKING_PARAMS.sub("", url)
    # Clean up leftover ? or &
    cleaned = re.sub(r"\?&", "?", cleaned)
    cleaned = re.sub(r"\?$", "", cleaned)
    return cleaned


def clean_record(record: AttorneyRecord) -> AttorneyRecord:
    """Apply all cleaning rules to a record."""
    # Phone
    if record.phone:
        record.phone = clean_phone(record.phone)

    # State
    if record.state:
        record.state = record.state.upper()

    # URLs: strip tracking params
    for field_name in ("firm_website_url", "professional_webpage_url",
                       "linkedin_url", "facebook_url", "twitter_url",
                       "findlaw_url", "profile_url"):
        val = getattr(record, field_name)
        if val:
            setattr(record, field_name, strip_tracking_params(val))

    # Auto-generated bio detection
    if record.about:
        for pattern in BOILERPLATE_PATTERNS:
            if pattern.search(record.about):
                record.about = ""
                break

    # Truncate oversized fields
    for f in fields(record):
        val = getattr(record, f.name)
        if isinstance(val, str) and len(val) > config.MAX_CELL_LENGTH:
            setattr(record, f.name, val[:config.MAX_CELL_LENGTH] + "... [truncated]")

    return record


def run(records_path: str, output_dir: str | None = None) -> str:
    """Clean records and export to CSV. Returns path to CSV file."""
    with open(records_path, encoding="utf-8") as f:
        raw_records = json.load(f)

    # Reconstruct AttorneyRecord objects
    valid_fields = {f.name for f in fields(AttorneyRecord)}
    records = []
    for data in raw_records:
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        record = AttorneyRecord(**filtered)
        record = clean_record(record)
        records.append(record)

    # Determine output path
    if not output_dir:
        output_dir = config.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    # Infer city/state from records or directory name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dirname = os.path.basename(os.path.dirname(records_path))
    filename = f"superlawyers_{dirname}_{timestamp}.csv"
    csv_path = os.path.join(output_dir, filename)

    # Write CSV
    with open(csv_path, "w", newline="", encoding=config.CSV_ENCODING) as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(AttorneyRecord.csv_headers())
        for record in records:
            writer.writerow(record.to_csv_row())

    log.info(f"CSV exported: {csv_path} ({len(records)} records)")
    return csv_path
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_export.py -v`
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
git add commands/export.py tests/test_export.py
git commit -m "feat: export command — data cleaning pipeline + CSV output"
```

---

### Task 13: CLI Entry Point

**Files:**
- Create: `cli.py`

**Step 1: Write implementation**

```python
# cli.py
"""CLI entry point for the Super Lawyers scraper."""

import argparse
import asyncio
import logging
import sys


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_discover(args):
    from commands.discover import run
    result = asyncio.run(run(args.location))
    print(f"Practice areas saved to: {result}")


def cmd_crawl_listings(args):
    from commands.crawl_listings import run
    result = asyncio.run(run(args.input))
    print(f"Listings saved to: {result}")


def cmd_fetch_profiles(args):
    from commands.fetch_profiles import run
    result = asyncio.run(run(args.input))
    print(f"HTML files saved to: {result}/html/")


def cmd_parse_profiles(args):
    from commands.parse_profiles import run
    result = run(args.data_dir)
    print(f"Records saved to: {result}")


def cmd_export(args):
    from commands.export import run
    result = run(args.input, args.output)
    print(f"CSV exported to: {result}")


def main():
    parser = argparse.ArgumentParser(
        description="Super Lawyers attorney directory scraper"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    # discover
    p_discover = sub.add_parser("discover", help="Resolve location and find practice areas")
    p_discover.add_argument("location", help='City, State (e.g., "Los Angeles, CA")')
    p_discover.set_defaults(func=cmd_discover)

    # crawl-listings
    p_crawl = sub.add_parser("crawl-listings", help="Crawl listing pages for profile URLs")
    p_crawl.add_argument("input", help="Path to practice_areas.json")
    p_crawl.set_defaults(func=cmd_crawl_listings)

    # fetch-profiles
    p_fetch = sub.add_parser("fetch-profiles", help="Download profile HTML to disk")
    p_fetch.add_argument("input", help="Path to listings.json")
    p_fetch.set_defaults(func=cmd_fetch_profiles)

    # parse-profiles
    p_parse = sub.add_parser("parse-profiles", help="Parse saved HTML into records")
    p_parse.add_argument("data_dir", help="Path to data directory (containing html/ and listings.json)")
    p_parse.set_defaults(func=cmd_parse_profiles)

    # export
    p_export = sub.add_parser("export", help="Clean records and export CSV")
    p_export.add_argument("input", help="Path to records.json")
    p_export.add_argument("-o", "--output", default=None, help="Output directory (default: ./output)")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
```

**Step 2: Verify CLI help works**

Run: `python cli.py --help`
Expected: Shows available commands (discover, crawl-listings, fetch-profiles, parse-profiles, export)

Run: `python cli.py discover --help`
Expected: Shows discover usage with location argument

**Step 3: Commit**

```bash
git add cli.py
git commit -m "feat: CLI entry point with argparse subcommands"
```

---

### Task 14: End-to-End Smoke Test

**Purpose:** Run the full pipeline against a small city to validate everything works together.

**Step 1: Pick a small test city**

Use a small city that should have few attorneys (faster, less load on the site). Example: `"Beverly Hills, CA"` or a smaller city.

**Step 2: Run each command sequentially**

```bash
python cli.py -v discover "Beverly Hills, CA"
# Verify: data/beverly-hills_ca/practice_areas.json exists with practice area list

python cli.py -v crawl-listings data/beverly-hills_ca/practice_areas.json
# Verify: data/beverly-hills_ca/listings.json exists with attorney UUIDs

python cli.py -v fetch-profiles data/beverly-hills_ca/listings.json
# Verify: data/beverly-hills_ca/html/ contains .html files
# Verify: data/beverly-hills_ca/fetch_status.json shows success/fail counts

python cli.py -v parse-profiles data/beverly-hills_ca/
# Verify: data/beverly-hills_ca/records.json exists with full records

python cli.py -v export data/beverly-hills_ca/records.json
# Verify: output/ contains CSV file with 33 columns + data rows
```

**Step 3: Validate CSV output**

Open the CSV and verify:
- Header has exactly 33 columns matching `AttorneyRecord.csv_headers()`
- Records have UUIDs, names, and profile URLs
- Phone numbers are formatted as XXX-XXX-XXXX
- No tracking params in URLs
- Auto-generated bios are empty strings
- `profile_tier` is one of: basic, expanded, premium

**Step 4: Commit any fixes**

If any step fails, fix the issue and commit. Add any newly-saved HTML files as additional test fixtures if they reveal selector issues.

**Step 5: Final commit**

```bash
git add -A
git commit -m "test: validate end-to-end pipeline against live site"
```

---

## Task Summary

| Task | What | Key Files | Tests |
|------|------|-----------|-------|
| 1 | Project scaffolding | requirements.txt, README.md, .gitignore | — |
| 2 | Config + data model | config.py, models.py | test_models.py (12 tests) |
| 3 | Validation spike | spike.py → fixtures/ | — (manual) |
| 4 | Address parser | parsers/address_parser.py | test_address_parser.py (6 tests) |
| 5 | Listing parser | parsers/listing_parser.py | test_listing_parser.py (6 tests) |
| 6 | Profile parser | parsers/profile_parser.py | test_profile_parser.py (5+ tests) |
| 7 | HTTP client | http_client.py | — (integration) |
| 8 | Discover command | commands/discover.py | — (integration) |
| 9 | Crawl listings command | commands/crawl_listings.py | — (integration) |
| 10 | Fetch profiles command | commands/fetch_profiles.py | — (integration) |
| 11 | Parse profiles command | commands/parse_profiles.py | — (integration) |
| 12 | Export command | commands/export.py | test_export.py (8 tests) |
| 13 | CLI entry point | cli.py | — (manual) |
| 14 | End-to-end smoke test | — | — (manual) |

> **STOP after Task 3.** Review spike output before continuing. Parser code in Tasks 4-6 must reflect the **actual** HTML structure, not assumptions.
