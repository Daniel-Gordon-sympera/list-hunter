# tests/test_listing_parser.py
"""Tests for the listing page parser."""

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


def test_parse_listing_page_count():
    """The fixture has 35 cards but 2 duplicates, so 33 unique attorneys."""
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    assert len(records) == 33


def test_listing_records_have_uuid():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.uuid, f"Record missing UUID: {r.name}"
        assert len(r.uuid) == 36, f"UUID wrong length: {r.uuid}"


def test_listing_records_unique_uuids():
    """Each record should have a unique UUID (duplicates deduplicated)."""
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    uuids = [r.uuid for r in records]
    assert len(uuids) == len(set(uuids)), "Found duplicate UUIDs in results"


def test_listing_records_have_profile_url():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.profile_url.startswith("https://profiles.superlawyers.com/")
        assert "/lawyer/" in r.profile_url, "Profile URL should contain /lawyer/"
        assert "?" not in r.profile_url, "Tracking params should be stripped"


def test_listing_records_have_name():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.name, f"Record missing name for UUID {r.uuid}"


def test_listing_records_have_firm_name():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.firm_name, f"Record missing firm_name for {r.name}"


def test_listing_records_have_phone():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.phone, f"Record missing phone for {r.name}"
        assert r.phone.isdigit(), f"Phone should be digits only: {r.phone}"
        assert len(r.phone) == 10, f"Phone should be 10 digits: {r.phone}"


def test_listing_records_have_description():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.description, f"Record missing description for {r.name}"


def test_listing_records_have_selection_type():
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    for r in records:
        assert r.selection_type in ("Super Lawyers", "Rising Stars", ""), \
            f"Unexpected selection_type: {r.selection_type}"


def test_sponsored_cards_have_selection_type():
    """Sponsored (top_spot/spot_light) cards should have selection_type set."""
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    # The first 10 cards are sponsored (top_spot + spot_light)
    # They all have icon-ribbon with "Sponsored Super Lawyers selectee"
    sponsored = [r for r in records if r.selection_type == "Super Lawyers"]
    assert len(sponsored) >= 10, \
        f"Expected at least 10 sponsored cards, got {len(sponsored)}"


def test_first_record_specific_values():
    """Check specific values for the first attorney (Toni Long)."""
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    first = records[0]
    assert first.uuid == "b734639a-e088-455a-9bea-d686c8f1b61c"
    assert first.name == "Toni Long"
    assert first.firm_name == "The Long Law Group, PC"
    assert first.phone == "2133282848"
    assert first.profile_url == (
        "https://profiles.superlawyers.com/california/pasadena/lawyer/"
        "toni-y-long/b734639a-e088-455a-9bea-d686c8f1b61c.html"
    )
    assert first.selection_type == "Super Lawyers"


def test_poap_record_specific_values():
    """Check specific values for a non-sponsored (poap) attorney."""
    html = _load_fixture("listing_page.html")
    records = parse_listing_page(html)
    # Find Navid Soleymani by UUID
    navid = next(
        r for r in records
        if r.uuid == "43cfa45c-ed30-4937-b922-1998368d5305"
    )
    assert navid.name == "Navid Soleymani"
    assert navid.firm_name == "Yadegar Minoofar & Soleymani LLP"
    assert navid.phone == "3104990140"
    assert navid.profile_url == (
        "https://profiles.superlawyers.com/california/los-angeles/lawyer/"
        "navid-soleymani/43cfa45c-ed30-4937-b922-1998368d5305.html"
    )
    # Non-sponsored cards have no ribbon, so selection_type is empty
    assert navid.selection_type == ""


def test_compact_basic_card_parsing():
    """Compact 'basic' cards should extract firm_name and selection_type."""
    html = '''<html><body>
    <div class="card serp-container basic lawyer">
      <h2 class="full-name fw-bold mb-0">
        <a class="directory_profile"
           href="https://profiles.superlawyers.com/new-york/new-york/lawyer/test/11111111-2222-3333-4444-555555555555.html"
           aria-label="View profile of John Test">John Test</a>
      </h2>
      <span class="d-block fw-bold text-secondary mt-1">
        Acme Legal LLP | <span class="city">New York, NY</span>
      </span>
      <span class="selected_to d-block"> Rising Stars </span>
    </div>
    </body></html>'''
    records = parse_listing_page(html)
    assert len(records) == 1
    r = records[0]
    assert r.name == "John Test"
    assert r.firm_name == "Acme Legal LLP"
    assert r.selection_type == "Rising Stars"
    assert r.phone == ""
    assert r.description == ""


def test_compact_card_super_lawyers_selection():
    """Compact card with 'Super Lawyers' selection type."""
    html = '''<html><body>
    <div class="card serp-container basic lawyer">
      <h2 class="full-name fw-bold mb-0">
        <a class="directory_profile"
           href="https://profiles.superlawyers.com/new-york/new-york/lawyer/jane/22222222-3333-4444-5555-666666666666.html"
           aria-label="View profile of Jane Doe">Jane Doe</a>
      </h2>
      <span class="d-block fw-bold text-secondary mt-1">
        Smith &amp; Partners | <span class="city">New York, NY</span>
      </span>
      <span class="selected_to d-block"> Super Lawyers </span>
    </div>
    </body></html>'''
    records = parse_listing_page(html)
    assert len(records) == 1
    assert records[0].firm_name == "Smith & Partners"
    assert records[0].selection_type == "Super Lawyers"


def test_compact_card_serving_city_variant():
    """Compact card with 'Serving' prefix in city span."""
    html = '''<html><body>
    <div class="card serp-container basic lawyer">
      <h2 class="full-name fw-bold mb-0">
        <a class="directory_profile"
           href="https://profiles.superlawyers.com/new-york/new-york/lawyer/bob/33333333-4444-5555-6666-777777777777.html">Bob Smith</a>
      </h2>
      <span class="d-block fw-bold text-secondary mt-1">
        Brooklyn Law Firm | <span class="city">Serving New York, NY (Brooklyn, NY)</span>
      </span>
      <span class="selected_to d-block"> Super Lawyers </span>
    </div>
    </body></html>'''
    records = parse_listing_page(html)
    assert len(records) == 1
    assert records[0].firm_name == "Brooklyn Law Firm"


def test_mixed_rich_and_compact_cards():
    """Mix of rich (a.single-link) and compact cards should all parse."""
    html = '''<html><body>
    <div class="card serp-container top_spot lawyer">
      <h2 class="full-name fw-bold mb-0">
        <a href="https://profiles.superlawyers.com/ca/la/lawyer/rich/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html">Rich Card</a>
      </h2>
      <a class="single-link" href="/firm">Big Firm LLC</a>
      <a href="tel:+12125551234">212-555-1234</a>
      <p class="ts_tagline">Expert attorney</p>
      <i class="icon-ribbon" aria-label="Sponsored Super Lawyers selectee"></i>
    </div>
    <div class="card serp-container basic lawyer">
      <h2 class="full-name fw-bold mb-0">
        <a class="directory_profile"
           href="https://profiles.superlawyers.com/ny/ny/lawyer/compact/ffffffff-1111-2222-3333-444444444444.html">Compact Card</a>
      </h2>
      <span class="d-block fw-bold text-secondary mt-1">
        Small Firm PC | <span class="city">New York, NY</span>
      </span>
      <span class="selected_to d-block"> Rising Stars </span>
    </div>
    </body></html>'''
    records = parse_listing_page(html)
    assert len(records) == 2
    rich = records[0]
    assert rich.name == "Rich Card"
    assert rich.firm_name == "Big Firm LLC"
    assert rich.phone == "2125551234"
    assert rich.description == "Expert attorney"
    assert rich.selection_type == "Super Lawyers"
    compact = records[1]
    assert compact.name == "Compact Card"
    assert compact.firm_name == "Small Firm PC"
    assert compact.phone == ""
    assert compact.description == ""
    assert compact.selection_type == "Rising Stars"


def test_empty_html_returns_empty():
    records = parse_listing_page("<html><body></body></html>")
    assert records == []


def test_no_cards_returns_empty():
    html = "<html><body><div>No attorney cards here</div></body></html>"
    records = parse_listing_page(html)
    assert records == []
