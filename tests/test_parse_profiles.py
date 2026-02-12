# tests/test_parse_profiles.py
"""Tests for parse_profiles command: Cloudflare skip + merge logic."""

import json
import os
import tempfile

import pytest

from commands.parse_profiles import merge_records, run
from models import AttorneyRecord


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

MINIMAL_PROFILE_HTML = """
<html>
<head><title>Attorney Profile</title></head>
<body>
<h1 id="attorney_name">Jane Smith</h1>
<a href="/lawfirm/smith-firm/abc.html">Smith & Associates</a>
</body>
</html>
"""


class TestMergeRecords:
    """Tests for the merge_records function."""

    def test_profile_wins_when_both_present(self):
        profile = AttorneyRecord(name="Profile Name", phone="111")
        listing = AttorneyRecord(name="Listing Name", phone="222")
        merged = merge_records(profile, listing)
        assert merged.name == "Profile Name"
        assert merged.phone == "111"

    def test_listing_fills_empty_profile_fields(self):
        profile = AttorneyRecord(name="", phone="")
        listing = AttorneyRecord(name="Listing Name", phone="222")
        merged = merge_records(profile, listing)
        assert merged.name == "Listing Name"
        assert merged.phone == "222"

    def test_listing_name_used_when_profile_name_empty(self):
        """After fix #2 (no generic h1 fallback), profile name will be empty
        for Cloudflare pages. Listing name should fill the gap."""
        profile = AttorneyRecord(uuid="abc", name="", firm_name="")
        listing = AttorneyRecord(uuid="abc", name="John Doe", firm_name="Doe LLP")
        merged = merge_records(profile, listing)
        assert merged.name == "John Doe"
        assert merged.firm_name == "Doe LLP"


class TestParseProfilesCloudflareSkip:
    """Integration test: parse_profiles skips Cloudflare challenge HTML files."""

    def test_cloudflare_html_uses_listing_data(self):
        """When an HTML file is a Cloudflare challenge, the record should use
        listing data only and not extract garbage from the challenge page."""
        with tempfile.TemporaryDirectory() as tmpdir:
            html_dir = os.path.join(tmpdir, "html")
            os.makedirs(html_dir)

            uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

            # Write Cloudflare challenge HTML
            with open(os.path.join(html_dir, f"{uuid}.html"), "w") as f:
                f.write(CLOUDFLARE_CHALLENGE_HTML)

            # Write listings.json with correct data
            listings = {
                uuid: {
                    "uuid": uuid,
                    "name": "John Doe",
                    "firm_name": "Doe & Associates",
                    "profile_url": f"https://profiles.superlawyers.com/x/{uuid}.html",
                    "city": "New York",
                    "state": "NY",
                }
            }
            listings_path = os.path.join(tmpdir, "listings.json")
            with open(listings_path, "w") as f:
                json.dump(listings, f)

            # Run parse-profiles
            output_path = run(tmpdir)

            # Read and verify
            with open(output_path) as f:
                records = json.load(f)

            assert len(records) == 1
            rec = records[0]
            assert rec["name"] == "John Doe"
            assert rec["firm_name"] == "Doe & Associates"
            assert rec["name"] != "profiles.superlawyers.com"

    def test_valid_html_parsed_normally(self):
        """A real profile HTML should be parsed normally (not skipped)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            html_dir = os.path.join(tmpdir, "html")
            os.makedirs(html_dir)

            uuid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"

            with open(os.path.join(html_dir, f"{uuid}.html"), "w") as f:
                f.write(MINIMAL_PROFILE_HTML)

            listings = {
                uuid: {
                    "uuid": uuid,
                    "name": "Listing Name",
                    "profile_url": f"https://profiles.superlawyers.com/x/{uuid}.html",
                }
            }
            listings_path = os.path.join(tmpdir, "listings.json")
            with open(listings_path, "w") as f:
                json.dump(listings, f)

            output_path = run(tmpdir)

            with open(output_path) as f:
                records = json.load(f)

            assert len(records) == 1
            # Profile-parsed name should win over listing name
            assert records[0]["name"] == "Jane Smith"
            assert records[0]["firm_name"] == "Smith & Associates"
