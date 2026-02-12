# tests/test_profile_parser.py
"""Tests for profile_parser using real fixture HTML."""

import os
import pytest
from parsers.profile_parser import parse_profile
from http_client import is_cloudflare_challenge

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


PROFILE_URL = (
    "https://profiles.superlawyers.com/california/pasadena/lawyer/"
    "toni-y-long/b734639a-e088-455a-9bea-d686c8f1b61c.html"
)


class TestProfileParserPremium:
    """Tests against the real premium profile fixture for Toni Y. Long."""

    def setup_method(self):
        html = _load_fixture("profile_premium.html")
        self.record = parse_profile(html, PROFILE_URL)

    # --- Group A: Identity ---

    def test_uuid_extracted(self):
        assert self.record.uuid == "b734639a-e088-455a-9bea-d686c8f1b61c"

    def test_name(self):
        assert self.record.name == "Toni Y. Long"

    def test_firm_name(self):
        assert self.record.firm_name == "The Long Law Group, PC"

    def test_selection_type(self):
        assert self.record.selection_type == "Rising Stars"

    def test_selection_years(self):
        assert self.record.selection_years == "2007"

    def test_description(self):
        assert "Business & Corporate" in self.record.description
        assert "Pasadena" in self.record.description

    # --- Group B: Location ---

    def test_street(self):
        assert "30 North Raymond Ave." in self.record.street
        assert "Suite 402" in self.record.street

    def test_city(self):
        assert self.record.city == "Pasadena"

    def test_state(self):
        assert self.record.state == "CA"

    def test_zip_code(self):
        assert self.record.zip_code == "91103"

    def test_country(self):
        assert self.record.country == "United States"

    def test_geo_coordinates(self):
        assert self.record.geo_coordinates == "34.146310,-118.148891"

    # --- Group C: Contact ---

    def test_phone(self):
        assert self.record.phone == "213-328-2848"

    def test_email_empty_when_absent(self):
        assert self.record.email == ""

    def test_firm_website_url(self):
        assert self.record.firm_website_url == "https://www.tyllaw.com"

    def test_professional_webpage_url(self):
        assert self.record.professional_webpage_url == "https://www.tyllaw.com/attorney/toni-y-long/"

    def test_profile_url(self):
        assert self.record.profile_url == PROFILE_URL
        assert "?" not in self.record.profile_url

    # --- Group D: Professional ---

    def test_about_contains_bio(self):
        assert "managing partner" in self.record.about
        assert "The Long Law Group" in self.record.about

    def test_about_has_multiple_paragraphs(self):
        # The about section has 5 paragraphs separated by double newlines
        assert "\n\n" in self.record.about

    def test_about_starts_correctly(self):
        assert self.record.about.startswith("Attorney Toni Y. Long")

    def test_practice_areas(self):
        pa = self.record.practice_areas
        assert "Business/Corporate" in pa
        assert "Entertainment & Sports" in pa
        assert "Employment & Labor" in pa
        assert "Mergers & Acquisitions" in pa

    def test_focus_areas(self):
        fa = self.record.focus_areas
        assert "Business Formation and Planning" in fa
        assert "Contracts" in fa
        assert "Limited Liability Companies" in fa
        assert "Sub-chapter S Corporations" in fa

    def test_licensed_since(self):
        assert self.record.licensed_since == "2001"

    def test_education(self):
        assert self.record.education == "University of California Los Angeles (UCLA) School of Law"

    def test_languages_empty_when_absent(self):
        assert self.record.languages == ""

    # --- Group E: Achievements ---

    def test_honors_not_empty(self):
        assert self.record.honors != ""

    def test_honors_contains_rising_star(self):
        assert "Rising Star" in self.record.honors

    def test_honors_contains_top_attorney(self):
        assert "Top Attorney" in self.record.honors

    def test_bar_activity(self):
        assert "California" in self.record.bar_activity

    def test_pro_bono_empty_when_absent(self):
        assert self.record.pro_bono == ""

    def test_publications_empty_when_absent(self):
        assert self.record.publications == ""

    # --- Group F: Social ---

    def test_linkedin_url(self):
        assert self.record.linkedin_url == "http://www.linkedin.com/in/tonilong/"

    def test_facebook_url(self):
        assert self.record.facebook_url == "https://www.facebook.com/thelonglawgroup/"

    def test_twitter_url(self):
        assert self.record.twitter_url == "https://twitter.com/thelonglawgroup"

    def test_findlaw_url(self):
        assert "lawyers.findlaw.com" in self.record.findlaw_url
        assert "toni-y-long" in self.record.findlaw_url

    # --- Group G: Metadata ---

    def test_profile_tier_is_premium(self):
        assert self.record.profile_tier == "premium"

    def test_scraped_at_is_set(self):
        assert self.record.scraped_at != ""

    def test_completeness_score_high(self):
        score = self.record.completeness_score()
        # Most fields are populated for this premium profile
        assert score >= 0.7

    def test_url_with_query_params_stripped(self):
        html = _load_fixture("profile_premium.html")
        record = parse_profile(html, PROFILE_URL + "?foo=bar")
        assert record.profile_url == PROFILE_URL


class TestProfileParserEmpty:
    """Tests for graceful handling of empty/minimal HTML."""

    def test_empty_html_returns_record(self):
        record = parse_profile(
            "<html><body></body></html>",
            "https://example.com/x/00000000-0000-0000-0000-000000000000.html",
        )
        assert record.uuid == "00000000-0000-0000-0000-000000000000"
        assert record.name == ""
        assert record.country == "United States"

    def test_empty_html_has_default_fields(self):
        record = parse_profile(
            "<html><body></body></html>",
            "https://example.com/x/00000000-0000-0000-0000-000000000000.html",
        )
        assert record.phone == ""
        assert record.firm_name == ""
        assert record.practice_areas == ""
        assert record.about == ""
        assert record.linkedin_url == ""

    def test_no_uuid_in_url(self):
        record = parse_profile(
            "<html><body></body></html>",
            "https://example.com/some-page.html",
        )
        assert record.uuid == ""


class TestSafeExtraction:
    """Tests for robustness of the parser against malformed content."""

    def test_survives_malformed_html(self):
        html = '<html><body><h1 id="attorney_name">Test Name</h1><div>broken<</div></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/11111111-2222-3333-4444-555555555555.html",
        )
        assert record.name == "Test Name"
        assert record.uuid == "11111111-2222-3333-4444-555555555555"

    def test_h1_with_extra_whitespace(self):
        html = '<html><body><h1 id="attorney_name">  Jane   Doe  </h1></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.name == "Jane   Doe"

    def test_phone_extracted_from_tel_link(self):
        html = '<html><body><a href="tel:+15551234567">555-123-4567</a></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.phone == "555-123-4567"

    def test_email_extracted_from_mailto_link(self):
        html = '<html><body><a href="mailto:test@example.com">Email me</a></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.email == "test@example.com"

    def test_geo_coordinates_from_maps_img(self):
        html = '<html><body><img src="https://maps.googleapis.com/maps/api/staticmap?center=40.7128,-74.0060&zoom=15"/></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.geo_coordinates == "40.7128,-74.0060"

    def test_selection_type_super_lawyers(self):
        html = '<html><body><span class="fst-italic">Selected to Super Lawyers: 2020 - 2023</span></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.selection_type == "Super Lawyers"
        assert record.selection_years == "2020 - 2023"

    def test_firm_name_from_lawfirm_link(self):
        html = '<html><body><a href="/lawfirm/test-firm/abc123.html">Test Firm LLP</a></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.firm_name == "Test Firm LLP"

    def test_about_from_about_div(self):
        html = '<html><body><div id="about"><p>First paragraph.</p><p>Second paragraph.</p></div></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.about == "First paragraph.\n\nSecond paragraph."

    def test_education_from_lawschool_link(self):
        html = '<html><body><a href="https://lawschools.superlawyers.com/law-school/test/abc.html">Harvard Law School</a></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.education == "Harvard Law School"

    def test_practice_areas_from_text_node(self):
        html = '<html><body><div id="practice-areas"><h3>Practice areas</h3> Criminal Defense, Family Law </div></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.practice_areas == "Criminal Defense, Family Law"


CLOUDFLARE_CHALLENGE_HTML = """
<html>
<head><title>Just a moment...</title></head>
<body>
<h1 class="zone-name-title h1">profiles.superlawyers.com</h1>
<div id="challenge-platform">
<p>Verifying you are human. This may take a few seconds.</p>
</div>
</body>
</html>
"""


class TestCloudflareDetection:
    """Tests for Cloudflare challenge page detection and handling."""

    def test_cloudflare_challenge_detected_by_title(self):
        html = "<html><head><title>Just a moment...</title></head><body></body></html>"
        assert is_cloudflare_challenge(html) is True

    def test_cloudflare_challenge_detected_by_platform(self):
        html = '<html><body><div id="challenge-platform"></div></body></html>'
        assert is_cloudflare_challenge(html) is True

    def test_cloudflare_challenge_detected_by_verifying(self):
        html = "<html><body><p>Verifying you are human</p></body></html>"
        assert is_cloudflare_challenge(html) is True

    def test_normal_profile_not_flagged(self):
        html = _load_fixture("profile_premium.html")
        assert is_cloudflare_challenge(html) is False

    def test_empty_html_not_flagged(self):
        assert is_cloudflare_challenge("<html><body></body></html>") is False

    def test_cloudflare_page_returns_empty_name(self):
        """After removing the generic h1 fallback, a Cloudflare challenge page
        must NOT extract 'profiles.superlawyers.com' as the attorney name."""
        record = parse_profile(
            CLOUDFLARE_CHALLENGE_HTML,
            "https://profiles.superlawyers.com/new-york/new-york/lawyer/john-doe/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.name == ""
        assert record.name != "profiles.superlawyers.com"

    def test_generic_h1_no_longer_used_for_name(self):
        """A bare <h1> (without id='attorney_name') should not be picked up."""
        html = '<html><body><h1>Some Random Heading</h1></body></html>'
        record = parse_profile(
            html,
            "https://example.com/x/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.html",
        )
        assert record.name == ""
