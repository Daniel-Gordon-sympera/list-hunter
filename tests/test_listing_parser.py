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


def test_empty_html_returns_empty():
    records = parse_listing_page("<html><body></body></html>")
    assert records == []


def test_no_cards_returns_empty():
    html = "<html><body><div>No attorney cards here</div></body></html>"
    records = parse_listing_page(html)
    assert records == []
