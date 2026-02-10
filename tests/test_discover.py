# tests/test_discover.py
"""Tests for the discover command — location parsing and practice area discovery."""

import json
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commands.discover import (
    discover_practice_areas,
    parse_location,
    run,
    STATE_ABBREV_TO_SLUG,
    SLUG_TO_ABBREV,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# parse_location tests
# ---------------------------------------------------------------------------


class TestParseLocation:
    def test_los_angeles_ca(self):
        assert parse_location("Los Angeles, CA") == ("california", "los-angeles")

    def test_los_angeles_full_state(self):
        assert parse_location("Los Angeles, California") == (
            "california",
            "los-angeles",
        )

    def test_new_york_ny(self):
        assert parse_location("New York, NY") == ("new-york", "new-york")

    def test_washington_dc(self):
        assert parse_location("Washington, DC") == ("washington-dc", "washington")

    def test_bad_input_no_comma(self):
        with pytest.raises(ValueError, match="Expected.*City, ST"):
            parse_location("bad input")

    def test_bad_input_invalid_state(self):
        with pytest.raises(ValueError, match="Unknown state"):
            parse_location("City, XX")

    def test_whitespace_handling(self):
        """Extra whitespace should be stripped."""
        assert parse_location("  Los Angeles ,  CA  ") == (
            "california",
            "los-angeles",
        )

    def test_case_insensitive_state(self):
        assert parse_location("Miami, fl") == ("florida", "miami")

    def test_all_50_states_plus_dc(self):
        """STATE_ABBREV_TO_SLUG has all 50 states + DC = 51 entries."""
        assert len(STATE_ABBREV_TO_SLUG) == 51

    def test_slug_to_abbrev_reverse_mapping(self):
        """SLUG_TO_ABBREV is the inverse of STATE_ABBREV_TO_SLUG."""
        assert len(SLUG_TO_ABBREV) == 51
        for abbrev, slug in STATE_ABBREV_TO_SLUG.items():
            assert SLUG_TO_ABBREV[slug] == abbrev


# ---------------------------------------------------------------------------
# discover_practice_areas tests (fixture-based, no network)
# ---------------------------------------------------------------------------


class TestDiscoverPracticeAreas:
    def test_fixture_finds_practice_areas(self):
        """The city_index.html fixture should yield practice area slugs."""
        html = _load_fixture("city_index.html")
        # Parse directly with regex to validate the fixture has the expected links
        pattern = re.compile(r"/([^/]+)/california/los-angeles/")
        matches = set(pattern.findall(html))
        # Remove known non-practice-area slugs
        matches.discard("search")
        matches.discard("advanced_search")
        matches.discard("california")
        assert len(matches) > 100, f"Expected >100 practice areas, got {len(matches)}"

    def test_fixture_contains_known_practice_areas(self):
        """Spot-check a few expected practice area slugs in the fixture."""
        html = _load_fixture("city_index.html")
        pattern = re.compile(r"/([^/]+)/california/los-angeles/")
        matches = set(pattern.findall(html))
        expected = {
            "criminal-defense",
            "family-law",
            "personal-injury-plaintiff",
            "bankruptcy",
            "real-estate",
            "immigration",
            "employment-and-labor",
        }
        assert expected.issubset(matches), f"Missing: {expected - matches}"

    def test_fixture_excludes_non_practice_areas(self):
        """The regex should not match city links like /california/los-angeles/."""
        html = _load_fixture("city_index.html")
        # Pattern for 3-segment practice area links
        pattern = re.compile(r"^/([^/]+)/california/los-angeles/$", re.MULTILINE)
        matches = set(pattern.findall(html))
        # These should not appear as practice area slugs
        assert "california" not in matches
        assert "search" not in matches

    @pytest.mark.asyncio
    async def test_discover_practice_areas_with_mock_client(self):
        """discover_practice_areas should parse HTML and return sorted slugs."""
        html = _load_fixture("city_index.html")
        client = AsyncMock()
        client.fetch = AsyncMock(return_value=html)

        result = await discover_practice_areas(client, "california", "los-angeles")

        assert isinstance(result, list)
        assert len(result) > 100
        assert result == sorted(result), "Results should be sorted"
        assert "criminal-defense" in result
        assert "family-law" in result
        # Verify exclusions
        assert "search" not in result
        assert "advanced_search" not in result
        assert "california" not in result

    @pytest.mark.asyncio
    async def test_discover_practice_areas_fetch_url(self):
        """discover_practice_areas should fetch the correct URL."""
        html = _load_fixture("city_index.html")
        client = AsyncMock()
        client.fetch = AsyncMock(return_value=html)

        await discover_practice_areas(client, "california", "los-angeles")

        client.fetch.assert_called_once()
        url = client.fetch.call_args[0][0]
        assert url == "https://attorneys.superlawyers.com/california/los-angeles/"

    @pytest.mark.asyncio
    async def test_discover_practice_areas_empty_html(self):
        """Empty or no-match HTML should return an empty list."""
        client = AsyncMock()
        client.fetch = AsyncMock(return_value="<html><body></body></html>")

        result = await discover_practice_areas(client, "california", "los-angeles")

        assert result == []

    @pytest.mark.asyncio
    async def test_discover_practice_areas_fetch_failure(self):
        """If fetch returns None, should raise RuntimeError."""
        client = AsyncMock()
        client.fetch = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="Failed to fetch"):
            await discover_practice_areas(client, "california", "los-angeles")


# ---------------------------------------------------------------------------
# run() integration tests (mocked I/O)
# ---------------------------------------------------------------------------


class TestRun:
    @pytest.mark.asyncio
    async def test_run_creates_output_file(self, tmp_path):
        """run() should create practice_areas.json in the data directory."""
        html = _load_fixture("city_index.html")
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(return_value=html)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        data_dir = str(tmp_path / "data")
        with patch("commands.discover.ScraperClient", return_value=mock_client), \
             patch("commands.discover.DATA_DIR", data_dir):
            output_path = await run("Los Angeles, CA")

        assert os.path.exists(output_path)
        assert output_path.endswith("practice_areas.json")

        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["state_slug"] == "california"
        assert data["city_slug"] == "los-angeles"
        assert isinstance(data["practice_areas"], list)
        assert len(data["practice_areas"]) > 100

    @pytest.mark.asyncio
    async def test_run_directory_structure(self, tmp_path):
        """run() should create city_state directory under DATA_DIR."""
        html = _load_fixture("city_index.html")
        mock_client = AsyncMock()
        mock_client.fetch = AsyncMock(return_value=html)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        data_dir = str(tmp_path / "data")
        with patch("commands.discover.ScraperClient", return_value=mock_client), \
             patch("commands.discover.DATA_DIR", data_dir):
            output_path = await run("Los Angeles, CA")

        expected_dir = os.path.join(data_dir, "los-angeles_ca")
        assert os.path.isdir(expected_dir)
        assert output_path == os.path.join(expected_dir, "practice_areas.json")
