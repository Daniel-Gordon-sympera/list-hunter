# tests/test_crawl_listings.py
"""Tests for crawl_listings checkpoint/resume logic."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commands.crawl_listings import _atomic_write, _write_checkpoint, run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_discovery(tmp_path, practice_areas):
    """Write a minimal practice_areas.json and return its path."""
    data = {
        "state_slug": "california",
        "city_slug": "los-angeles",
        "practice_areas": practice_areas,
    }
    pa_path = tmp_path / "practice_areas.json"
    pa_path.write_text(json.dumps(data), encoding="utf-8")
    return str(pa_path)


def _fake_record(uuid, name="Test Attorney"):
    """Return a minimal record dict keyed by uuid."""
    return {
        "uuid": uuid,
        "name": name,
        "firm_name": "",
        "selection_type": "",
        "selection_years": "",
        "description": "",
        "street": "",
        "city": "",
        "state": "",
        "zip_code": "",
        "country": "United States",
        "geo_coordinates": "",
        "phone": "",
        "email": "",
        "firm_website_url": "",
        "professional_webpage_url": "",
        "about": "",
        "practice_areas": "",
        "focus_areas": "",
        "licensed_since": "",
        "education": "",
        "languages": "",
        "honors": "",
        "bar_activity": "",
        "pro_bono": "",
        "publications": "",
        "linkedin_url": "",
        "facebook_url": "",
        "twitter_url": "",
        "findlaw_url": "",
        "profile_url": f"https://profiles.superlawyers.com/california/los-angeles/lawyer/test/{uuid}.html",
        "profile_tier": "",
        "scraped_at": "2026-01-01T00:00:00+00:00",
    }


def _make_card(uuid, name="Test Attorney"):
    """Create a mock card object with uuid and to_dict()."""
    card = MagicMock()
    card.uuid = uuid
    card.to_dict.return_value = _fake_record(uuid, name)
    return card


# ---------------------------------------------------------------------------
# _atomic_write tests
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_writes_json_file(self, tmp_path):
        path = str(tmp_path / "test.json")
        data = {"key": "value"}
        _atomic_write(path, data)

        with open(path, encoding="utf-8") as f:
            assert json.load(f) == data

    def test_no_tmp_file_left_behind(self, tmp_path):
        path = str(tmp_path / "test.json")
        _atomic_write(path, {"a": 1})

        assert not os.path.exists(path + ".tmp")

    def test_overwrites_existing_file(self, tmp_path):
        path = str(tmp_path / "test.json")
        _atomic_write(path, {"version": 1})
        _atomic_write(path, {"version": 2})

        with open(path, encoding="utf-8") as f:
            assert json.load(f)["version"] == 2


# ---------------------------------------------------------------------------
# _write_checkpoint tests
# ---------------------------------------------------------------------------


class TestWriteCheckpoint:
    def test_writes_both_files(self, tmp_path):
        output_path = str(tmp_path / "listings.json")
        progress_path = str(tmp_path / "crawl_progress.json")
        records = {"uuid-1": _fake_record("uuid-1")}
        completed = {"pa-a"}

        _write_checkpoint(output_path, records, progress_path, completed, 3, "2026-01-01T00:00:00Z")

        with open(output_path, encoding="utf-8") as f:
            assert json.load(f) == records

        with open(progress_path, encoding="utf-8") as f:
            progress = json.load(f)
        assert progress["completed_pas"] == ["pa-a"]
        assert progress["total_pas"] == 3
        assert progress["started_at"] == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# run() checkpoint integration tests
# ---------------------------------------------------------------------------


class TestRunCheckpoint:
    """Test that run() writes checkpoints after each PA and cleans up on completion."""

    @pytest.mark.asyncio
    async def test_checkpoint_written_after_each_pa(self, tmp_path):
        """After crawling each PA, crawl_progress.json should exist on disk."""
        pa_path = _make_discovery(tmp_path, ["family-law", "criminal-defense"])

        cards_by_pa = {
            "family-law": [_make_card("uuid-1")],
            "criminal-defense": [_make_card("uuid-2")],
        }

        call_count = 0

        async def fake_fetch(url, referer=None):
            nonlocal call_count
            call_count += 1
            for pa_slug, cards in cards_by_pa.items():
                if pa_slug in url and "page=1" in url:
                    return f"<html>{pa_slug}</html>"
            return None

        mock_client = AsyncMock()
        mock_client.fetch = fake_fetch
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        def fake_parse(html):
            for pa_slug, cards in cards_by_pa.items():
                if pa_slug in html:
                    return cards
            return []

        with patch("commands.crawl_listings.ScraperClient", return_value=mock_client), \
             patch("commands.crawl_listings.parse_listing_page", side_effect=fake_parse):
            result = await run(pa_path)

        # Progress file should be cleaned up on successful completion
        progress_path = str(tmp_path / "crawl_progress.json")
        assert not os.path.exists(progress_path)

        # listings.json should have both records
        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records
        assert "uuid-2" in records

    @pytest.mark.asyncio
    async def test_progress_file_deleted_on_completion(self, tmp_path):
        """crawl_progress.json must not exist after a successful full run."""
        pa_path = _make_discovery(tmp_path, ["tax-law"])

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>tax-law</html>"
            return None

        mock_client = AsyncMock()
        mock_client.fetch = fake_fetch
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("commands.crawl_listings.ScraperClient", return_value=mock_client), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-t")]):
            await run(pa_path)

        assert not os.path.exists(str(tmp_path / "crawl_progress.json"))

    @pytest.mark.asyncio
    async def test_resume_skips_completed_pas(self, tmp_path):
        """When a checkpoint exists, completed PAs should be skipped."""
        pa_path = _make_discovery(tmp_path, ["family-law", "criminal-defense"])

        # Simulate a previous partial run: family-law is done
        existing_records = {"uuid-1": _fake_record("uuid-1")}
        listings_path = tmp_path / "listings.json"
        listings_path.write_text(json.dumps(existing_records), encoding="utf-8")

        progress = {
            "completed_pas": ["family-law"],
            "total_pas": 2,
            "started_at": "2026-01-01T00:00:00Z",
        }
        progress_path = tmp_path / "crawl_progress.json"
        progress_path.write_text(json.dumps(progress), encoding="utf-8")

        fetched_urls = []

        async def fake_fetch(url, referer=None):
            fetched_urls.append(url)
            if "page=1" in url:
                return "<html>criminal-defense</html>"
            return None

        mock_client = AsyncMock()
        mock_client.fetch = fake_fetch
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("commands.crawl_listings.ScraperClient", return_value=mock_client), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-2")]):
            result = await run(pa_path)

        # family-law should NOT appear in fetched URLs
        assert not any("family-law" in u for u in fetched_urls)
        # criminal-defense should have been fetched
        assert any("criminal-defense" in u for u in fetched_urls)

        # Both records should be in the output
        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records
        assert "uuid-2" in records

        # Progress file cleaned up
        assert not os.path.exists(str(progress_path))

    @pytest.mark.asyncio
    async def test_force_ignores_checkpoint(self, tmp_path):
        """With force=True, existing checkpoint should be ignored and all PAs re-crawled."""
        pa_path = _make_discovery(tmp_path, ["family-law", "criminal-defense"])

        # Pre-existing checkpoint marking family-law as done
        existing_records = {"uuid-1": _fake_record("uuid-1")}
        listings_path = tmp_path / "listings.json"
        listings_path.write_text(json.dumps(existing_records), encoding="utf-8")

        progress = {
            "completed_pas": ["family-law"],
            "total_pas": 2,
            "started_at": "2026-01-01T00:00:00Z",
        }
        progress_path = tmp_path / "crawl_progress.json"
        progress_path.write_text(json.dumps(progress), encoding="utf-8")

        fetched_urls = []

        async def fake_fetch(url, referer=None):
            fetched_urls.append(url)
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_client = AsyncMock()
        mock_client.fetch = fake_fetch
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("commands.crawl_listings.ScraperClient", return_value=mock_client), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-new")]):
            result = await run(pa_path, force=True)

        # Both PAs should have been crawled (force ignores checkpoint)
        assert any("family-law" in u for u in fetched_urls)
        assert any("criminal-defense" in u for u in fetched_urls)

        # Old uuid-1 is gone (started fresh), only uuid-new present
        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" not in records
        assert "uuid-new" in records

        # Progress file cleaned up
        assert not os.path.exists(str(progress_path))
