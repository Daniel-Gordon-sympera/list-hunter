# tests/test_crawl_listings.py
"""Tests for crawl_listings: parallel crawling, PA filter, max results, resume, httpx fast path."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commands.crawl_listings import (
    CrawlState,
    _atomic_write,
    _cleanup_pa_files,
    _find_completed_pa_files,
    _httpx_fetch_listing_page,
    _merge_pa_files,
    run,
)


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


def _mock_scraper_pool(fake_fetch):
    """Build a mock ScraperPool that uses the given fake_fetch function."""
    mock_pool = AsyncMock()
    mock_pool.fetch = fake_fetch
    mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
    mock_pool.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


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
# CrawlState tests
# ---------------------------------------------------------------------------


class TestCrawlState:
    def test_should_stop_initially_false(self):
        state = CrawlState()
        assert not state.should_stop()

    def test_add_uuids_triggers_stop(self):
        state = CrawlState(max_results=3)
        assert state.add_uuids({"a", "b", "c"}) is True
        assert state.should_stop() is True

    def test_add_uuids_no_trigger_below_limit(self):
        state = CrawlState(max_results=10)
        assert state.add_uuids({"a", "b"}) is False
        assert not state.should_stop()

    def test_add_uuids_no_limit(self):
        state = CrawlState(max_results=None)
        assert state.add_uuids({f"uuid-{i}" for i in range(1000)}) is False
        assert not state.should_stop()

    def test_add_uuids_accumulates_across_calls(self):
        state = CrawlState(max_results=5)
        assert state.add_uuids({"a", "b"}) is False
        assert state.add_uuids({"b", "c"}) is False  # "b" is a dupe
        assert len(state.global_uuids) == 3
        assert state.add_uuids({"d", "e"}) is True
        assert len(state.global_uuids) == 5


# ---------------------------------------------------------------------------
# _find_completed_pa_files / _merge_pa_files / _cleanup_pa_files
# ---------------------------------------------------------------------------


class TestPerPaFiles:
    def test_find_completed_pa_files(self, tmp_path):
        (tmp_path / "listings_family-law.json").write_text("{}", encoding="utf-8")
        (tmp_path / "listings_tax-law.json").write_text("{}", encoding="utf-8")
        (tmp_path / "listings.json").write_text("{}", encoding="utf-8")  # not a PA file

        found = _find_completed_pa_files(str(tmp_path))
        assert "family-law" in found
        assert "tax-law" in found
        assert len(found) == 2

    def test_merge_pa_files_deduplicates(self, tmp_path):
        pa1 = {"uuid-1": _fake_record("uuid-1"), "uuid-2": _fake_record("uuid-2")}
        pa2 = {"uuid-2": _fake_record("uuid-2", "Duplicate"), "uuid-3": _fake_record("uuid-3")}

        (tmp_path / "listings_aa-law.json").write_text(json.dumps(pa1), encoding="utf-8")
        (tmp_path / "listings_bb-law.json").write_text(json.dumps(pa2), encoding="utf-8")

        merged = _merge_pa_files(str(tmp_path))
        assert len(merged) == 3
        # uuid-2 from pa1 should win (first occurrence)
        assert merged["uuid-2"]["name"] == "Test Attorney"

    def test_merge_pa_files_applies_max_results(self, tmp_path):
        pa = {f"uuid-{i}": _fake_record(f"uuid-{i}") for i in range(10)}
        (tmp_path / "listings_test.json").write_text(json.dumps(pa), encoding="utf-8")

        merged = _merge_pa_files(str(tmp_path), max_results=5)
        assert len(merged) == 5

    def test_cleanup_removes_pa_files(self, tmp_path):
        (tmp_path / "listings_family-law.json").write_text("{}", encoding="utf-8")
        (tmp_path / "listings_tax-law.json").write_text("{}", encoding="utf-8")
        (tmp_path / "crawl_progress.json").write_text("{}", encoding="utf-8")
        (tmp_path / "listings.json").write_text("{}", encoding="utf-8")  # should NOT be removed

        _cleanup_pa_files(str(tmp_path))

        assert not (tmp_path / "listings_family-law.json").exists()
        assert not (tmp_path / "listings_tax-law.json").exists()
        assert not (tmp_path / "crawl_progress.json").exists()
        assert (tmp_path / "listings.json").exists()  # preserved


# ---------------------------------------------------------------------------
# PA Filter tests
# ---------------------------------------------------------------------------


class TestPaFilter:
    @pytest.mark.asyncio
    async def test_filter_limits_pas_crawled(self, tmp_path):
        """Only specified PAs should be crawled."""
        pa_path = _make_discovery(tmp_path, ["family-law", "tax-law", "criminal-defense"])

        fetched_urls = []

        async def fake_fetch(url, referer=None):
            fetched_urls.append(url)
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-1")]):
            await run(pa_path, pa_filter=["family-law", "tax-law"], no_httpx=True)

        # criminal-defense should NOT appear
        assert not any("criminal-defense" in u for u in fetched_urls)
        assert any("family-law" in u for u in fetched_urls)
        assert any("tax-law" in u for u in fetched_urls)

    @pytest.mark.asyncio
    async def test_filter_unknown_slugs_warns(self, tmp_path):
        """Unknown PA slugs should be logged as warnings."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-1")]), \
             patch("commands.crawl_listings.log") as mock_log:
            await run(pa_path, pa_filter=["family-law", "nonexistent-law"], no_httpx=True)
            mock_log.warning.assert_any_call(
                "Unknown practice area slugs (ignored): %s", ["nonexistent-law"]
            )


# ---------------------------------------------------------------------------
# Max Results tests
# ---------------------------------------------------------------------------


class TestMaxResults:
    @pytest.mark.asyncio
    async def test_max_results_trims_output(self, tmp_path):
        """Final listings.json should have at most max_results entries."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        cards = [_make_card(f"uuid-{i}") for i in range(10)]

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=cards):
            result = await run(pa_path, max_results=5, no_httpx=True)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) <= 5

    @pytest.mark.asyncio
    async def test_max_results_none_returns_all(self, tmp_path):
        """Without max_results, all records should be returned."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        cards = [_make_card(f"uuid-{i}") for i in range(5)]

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=cards):
            result = await run(pa_path, max_results=None, no_httpx=True)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 5


# ---------------------------------------------------------------------------
# Parallel crawling tests
# ---------------------------------------------------------------------------


class TestParallelCrawling:
    @pytest.mark.asyncio
    async def test_multiple_pas_produce_merged_output(self, tmp_path):
        """Multiple PAs should produce a merged listings.json with all unique records."""
        pa_path = _make_discovery(tmp_path, ["family-law", "criminal-defense"])

        cards_by_pa = {
            "family-law": [_make_card("uuid-1")],
            "criminal-defense": [_make_card("uuid-2")],
        }

        async def fake_fetch(url, referer=None):
            for pa_slug in cards_by_pa:
                if pa_slug in url and "page=1" in url:
                    return f"<html>{pa_slug}</html>"
            return None

        def fake_parse(html):
            for pa_slug, cards in cards_by_pa.items():
                if pa_slug in html:
                    return cards
            return []

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", side_effect=fake_parse):
            result = await run(pa_path, workers=2, no_httpx=True)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records
        assert "uuid-2" in records

    @pytest.mark.asyncio
    async def test_per_pa_files_cleaned_up(self, tmp_path):
        """Per-PA listing files should be deleted after merge."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-1")]):
            await run(pa_path, no_httpx=True)

        # Per-PA file should be cleaned up
        assert not (tmp_path / "listings_family-law.json").exists()
        # Final output should exist
        assert (tmp_path / "listings.json").exists()


# ---------------------------------------------------------------------------
# Resume tests
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_skips_completed_pas(self, tmp_path):
        """PAs with existing per-PA files should be skipped on resume."""
        pa_path = _make_discovery(tmp_path, ["family-law", "criminal-defense"])

        # Pre-create per-PA file for family-law (simulating prior run)
        existing = {"uuid-1": _fake_record("uuid-1")}
        (tmp_path / "listings_family-law.json").write_text(
            json.dumps(existing), encoding="utf-8"
        )

        fetched_urls = []

        async def fake_fetch(url, referer=None):
            fetched_urls.append(url)
            if "page=1" in url:
                return "<html>criminal-defense</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-2")]):
            result = await run(pa_path, no_httpx=True)

        # family-law should NOT have been fetched
        assert not any("family-law" in u for u in fetched_urls)
        assert any("criminal-defense" in u for u in fetched_urls)

        # Both records should be in the merged output
        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records
        assert "uuid-2" in records

    @pytest.mark.asyncio
    async def test_force_ignores_existing_pa_files(self, tmp_path):
        """force=True should clean up per-PA files and re-crawl all."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        # Pre-create per-PA file
        (tmp_path / "listings_family-law.json").write_text(
            json.dumps({"uuid-old": _fake_record("uuid-old")}),
            encoding="utf-8",
        )

        fetched_urls = []

        async def fake_fetch(url, referer=None):
            fetched_urls.append(url)
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-new")]):
            result = await run(pa_path, force=True, no_httpx=True)

        # Should have re-crawled family-law
        assert any("family-law" in u for u in fetched_urls)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        # Old record should be gone, new record present
        assert "uuid-old" not in records
        assert "uuid-new" in records

    @pytest.mark.asyncio
    async def test_progress_file_deleted_on_completion(self, tmp_path):
        """crawl_progress.json must not exist after a successful run."""
        pa_path = _make_discovery(tmp_path, ["tax-law"])

        # Pre-create a stale progress file
        (tmp_path / "crawl_progress.json").write_text("{}", encoding="utf-8")

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>tax-law</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-t")]):
            await run(pa_path, no_httpx=True)

        assert not (tmp_path / "crawl_progress.json").exists()


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_cross_pa_dedup_at_merge(self, tmp_path):
        """Same UUID across PAs should result in one record in final output."""
        pa_path = _make_discovery(tmp_path, ["family-law", "criminal-defense"])

        # Both PAs return the same UUID
        shared_card = _make_card("uuid-shared")

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[shared_card]):
            result = await run(pa_path, workers=2, no_httpx=True)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert len(records) == 1
        assert "uuid-shared" in records


# ---------------------------------------------------------------------------
# _httpx_fetch_listing_page tests
# ---------------------------------------------------------------------------


class TestHttpxFetchListingPage:
    @pytest.mark.asyncio
    async def test_success_returns_html(self):
        """Successful httpx fetch returns (html, 'success')."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>listing page</body></html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        html, status = await _httpx_fetch_listing_page(
            mock_client, "https://example.com/page", referer="https://example.com/",
        )
        assert status == "success"
        assert html == "<html><body>listing page</body></html>"
        # Verify referer header was passed
        mock_client.get.assert_awaited_once_with(
            "https://example.com/page",
            headers={"Referer": "https://example.com/"},
        )

    @pytest.mark.asyncio
    async def test_cf_blocked_returns_none(self):
        """CF challenge response returns (None, 'cf_blocked')."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><title>Just a moment...</title></html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        html, status = await _httpx_fetch_listing_page(mock_client, "https://example.com/page")
        assert status == "cf_blocked"
        assert html is None

    @pytest.mark.asyncio
    async def test_cf_header_detected(self):
        """CF mitigation header triggers cf_blocked."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>normal</body></html>"
        mock_response.headers = {"cf-mitigated": "challenge"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        html, status = await _httpx_fetch_listing_page(mock_client, "https://example.com/page")
        assert status == "cf_blocked"
        assert html is None

    @pytest.mark.asyncio
    async def test_404_returns_failed(self):
        """404 response returns (None, 'failed')."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        html, status = await _httpx_fetch_listing_page(mock_client, "https://example.com/page")
        assert status == "failed"
        assert html is None

    @pytest.mark.asyncio
    async def test_500_returns_failed(self):
        """Server error returns (None, 'failed')."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        html, status = await _httpx_fetch_listing_page(mock_client, "https://example.com/page")
        assert status == "failed"
        assert html is None

    @pytest.mark.asyncio
    async def test_exception_returns_failed(self):
        """Network exception returns (None, 'failed')."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))

        html, status = await _httpx_fetch_listing_page(mock_client, "https://example.com/page")
        assert status == "failed"
        assert html is None

    @pytest.mark.asyncio
    async def test_no_referer_sends_empty_headers(self):
        """When no referer is given, no Referer header is sent."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>ok</html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        await _httpx_fetch_listing_page(mock_client, "https://example.com/page")
        mock_client.get.assert_awaited_once_with(
            "https://example.com/page",
            headers={},
        )


# ---------------------------------------------------------------------------
# httpx Fast Path integration tests
# ---------------------------------------------------------------------------


class TestHttpxFastPath:
    @pytest.mark.asyncio
    async def test_httpx_success_skips_browser(self, tmp_path):
        """When httpx succeeds, browser should not be called."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        browser_urls = []

        async def fake_browser_fetch(url, referer=None):
            browser_urls.append(url)
            return "<html>browser</html>"

        mock_pool = _mock_scraper_pool(fake_browser_fetch)

        async def fake_httpx_fetch(client, url, referer=None):
            if "page=1" in url:
                return "<html>family-law httpx</html>", "success"
            return None, "failed"

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-1")]), \
             patch("commands.crawl_listings.httpx") as mock_httpx:
            # Set up the httpx.AsyncClient mock
            mock_httpx_client = AsyncMock()
            mock_httpx_cm = MagicMock()
            mock_httpx_cm.__aenter__ = AsyncMock(return_value=mock_httpx_client)
            mock_httpx_cm.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_httpx_cm

            result = await run(pa_path, no_httpx=False)

        # Browser should not have been called for page 1 (httpx succeeded)
        assert not any("page=1" in u for u in browser_urls)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records

    @pytest.mark.asyncio
    async def test_cf_blocked_falls_back_to_browser(self, tmp_path):
        """When httpx returns cf_blocked, browser fallback should be used."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        browser_urls = []

        async def fake_browser_fetch(url, referer=None):
            browser_urls.append(url)
            if "page=1" in url:
                return "<html>browser page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_browser_fetch)

        async def fake_httpx_fetch(client, url, referer=None):
            return None, "cf_blocked"

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings._httpx_fetch_listing_page", side_effect=fake_httpx_fetch), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-1")]), \
             patch("commands.crawl_listings.httpx") as mock_httpx:
            mock_httpx_client = AsyncMock()
            mock_httpx_cm = MagicMock()
            mock_httpx_cm.__aenter__ = AsyncMock(return_value=mock_httpx_client)
            mock_httpx_cm.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_httpx_cm

            result = await run(pa_path, no_httpx=False)

        # Browser SHOULD have been called as fallback
        assert any("page=1" in u for u in browser_urls)

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records

    @pytest.mark.asyncio
    async def test_no_httpx_skips_httpx(self, tmp_path):
        """With no_httpx=True, httpx should not be used at all."""
        pa_path = _make_discovery(tmp_path, ["family-law"])

        async def fake_fetch(url, referer=None):
            if "page=1" in url:
                return "<html>page</html>"
            return None

        mock_pool = _mock_scraper_pool(fake_fetch)

        with patch("commands.crawl_listings.ScraperPool", return_value=mock_pool), \
             patch("commands.crawl_listings.parse_listing_page", return_value=[_make_card("uuid-1")]), \
             patch("commands.crawl_listings._httpx_fetch_listing_page") as mock_httpx_fetch:
            result = await run(pa_path, no_httpx=True)

        # _httpx_fetch_listing_page should never have been called
        mock_httpx_fetch.assert_not_called()

        with open(result, encoding="utf-8") as f:
            records = json.load(f)
        assert "uuid-1" in records
