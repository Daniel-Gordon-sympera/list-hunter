# tests/test_fetch_profiles.py
"""Tests for commands/fetch_profiles.py: idempotency, flags, status tracking, error handling."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from commands.fetch_profiles import run, _fetch_one


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_listings(tmp_path, uuids):
    """Write a listings.json with the given UUIDs and return its path."""
    listings = {
        uuid: {
            "name": f"Attorney {uuid[:8]}",
            "profile_url": f"https://profiles.superlawyers.com/test/{uuid}.html",
        }
        for uuid in uuids
    }
    path = tmp_path / "listings.json"
    path.write_text(json.dumps(listings), encoding="utf-8")
    return str(path)


def _mock_pool(fetch_side_effect=None, fetch_return_value="<html>profile</html>"):
    """Build a mock ScraperPool async context manager."""
    mock = AsyncMock()
    if fetch_side_effect is not None:
        mock.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock.fetch = AsyncMock(return_value=fetch_return_value)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------

class TestIdempotency:
    @pytest.mark.asyncio
    async def test_skips_existing_html_files(self, tmp_path):
        """UUIDs with existing HTML on disk get 'skipped' status."""
        uuid = "aaaa-bbbb-cccc-dddd"
        listings_path = _make_listings(tmp_path, [uuid])

        # Pre-create the HTML file
        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / f"{uuid}.html").write_text("<html>existing</html>", encoding="utf-8")

        mock = _mock_pool()
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock):
            await run(listings_path, no_httpx=True)

        # Should not have called fetch
        mock.fetch.assert_not_awaited()

        # Status should be 'skipped'
        status_path = tmp_path / "fetch_status.json"
        statuses = json.loads(status_path.read_text(encoding="utf-8"))
        assert statuses[uuid] == "skipped"

    @pytest.mark.asyncio
    async def test_force_redownloads_existing(self, tmp_path):
        """force=True fetches even when HTML exists on disk."""
        uuid = "aaaa-bbbb-cccc-dddd"
        listings_path = _make_listings(tmp_path, [uuid])

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        (html_dir / f"{uuid}.html").write_text("<html>old</html>", encoding="utf-8")

        mock = _mock_pool(fetch_return_value="<html>new</html>")
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            await run(listings_path, force=True, no_httpx=True)

        # Should have fetched
        mock.fetch.assert_awaited_once()

        # HTML file should be updated
        content = (html_dir / f"{uuid}.html").read_text(encoding="utf-8")
        assert content == "<html>new</html>"

        # Status should be 'success'
        statuses = json.loads((tmp_path / "fetch_status.json").read_text(encoding="utf-8"))
        assert statuses[uuid] == "success"


# ---------------------------------------------------------------------------
# retry-cf flag test
# ---------------------------------------------------------------------------

class TestRetryCf:
    @pytest.mark.asyncio
    async def test_retry_cf_redownloads_challenge_pages(self, tmp_path):
        """retry_cf=True re-fetches CF pages, keeps clean pages."""
        cf_uuid = "cf-uuid-1111-2222"
        clean_uuid = "clean-uuid-3333-4444"
        listings_path = _make_listings(tmp_path, [cf_uuid, clean_uuid])

        html_dir = tmp_path / "html"
        html_dir.mkdir()
        # CF challenge page
        (html_dir / f"{cf_uuid}.html").write_text(
            "<title>Just a moment...</title>", encoding="utf-8"
        )
        # Clean page
        (html_dir / f"{clean_uuid}.html").write_text(
            "<html>clean profile</html>", encoding="utf-8"
        )

        mock = _mock_pool(fetch_return_value="<html>fresh profile</html>")
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            await run(listings_path, retry_cf=True, no_httpx=True)

        # Only the CF page should have been re-fetched
        assert mock.fetch.await_count == 1

        statuses = json.loads((tmp_path / "fetch_status.json").read_text(encoding="utf-8"))
        assert statuses[cf_uuid] == "success"
        assert statuses[clean_uuid] == "skipped"


# ---------------------------------------------------------------------------
# Fetch success/failure tests
# ---------------------------------------------------------------------------

class TestFetchOutcomes:
    @pytest.mark.asyncio
    async def test_successful_fetch_writes_html(self, tmp_path):
        """Fetched HTML saved to html/{uuid}.html."""
        uuid = "new-uuid-5555-6666"
        listings_path = _make_listings(tmp_path, [uuid])

        mock = _mock_pool(fetch_return_value="<html>fetched</html>")
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            await run(listings_path, no_httpx=True)

        html_path = tmp_path / "html" / f"{uuid}.html"
        assert html_path.exists()
        assert html_path.read_text(encoding="utf-8") == "<html>fetched</html>"

    @pytest.mark.asyncio
    async def test_failed_fetch_records_status(self, tmp_path):
        """client.fetch() returns None -> 'failed' status."""
        uuid = "fail-uuid-7777-8888"
        listings_path = _make_listings(tmp_path, [uuid])

        mock = _mock_pool(fetch_return_value=None)
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            await run(listings_path, no_httpx=True)

        statuses = json.loads((tmp_path / "fetch_status.json").read_text(encoding="utf-8"))
        assert statuses[uuid] == "failed"

        # HTML file should not exist
        assert not (tmp_path / "html" / f"{uuid}.html").exists()

    @pytest.mark.asyncio
    async def test_fetch_status_json_written(self, tmp_path):
        """fetch_status.json has correct uuid->status mapping."""
        uuids = ["uuid-a", "uuid-b"]
        listings_path = _make_listings(tmp_path, uuids)

        # uuid-a succeeds, uuid-b fails
        async def side_effect(url, referer=None):
            if "uuid-a" in url:
                return "<html>profile a</html>"
            return None

        mock = _mock_pool(fetch_side_effect=side_effect)
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            await run(listings_path, no_httpx=True)

        status_path = tmp_path / "fetch_status.json"
        assert status_path.exists()
        statuses = json.loads(status_path.read_text(encoding="utf-8"))
        assert statuses["uuid-a"] == "success"
        assert statuses["uuid-b"] == "failed"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_listings_no_fetches(self, tmp_path):
        """Empty listings dict -> no ScraperPool instantiation."""
        path = tmp_path / "listings.json"
        path.write_text("{}", encoding="utf-8")

        mock = _mock_pool()
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock):
            await run(str(path), no_httpx=True)

        # ScraperPool context should never be entered
        mock.__aenter__.assert_not_awaited()

        # fetch_status.json still written (empty)
        statuses = json.loads((tmp_path / "fetch_status.json").read_text(encoding="utf-8"))
        assert statuses == {}

    @pytest.mark.asyncio
    async def test_exception_in_fetch_one_handled(self, tmp_path):
        """gather catches exception from one task, other fetches continue."""
        uuids = ["ok-uuid", "error-uuid"]
        listings_path = _make_listings(tmp_path, uuids)

        call_count = 0

        async def side_effect(url, referer=None):
            nonlocal call_count
            call_count += 1
            if "error-uuid" in url:
                raise RuntimeError("unexpected error")
            return "<html>good</html>"

        mock = _mock_pool(fetch_side_effect=side_effect)
        with patch("commands.fetch_profiles.ScraperPool", return_value=mock), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            await run(listings_path, no_httpx=True)

        # Both fetches were attempted
        assert call_count == 2

        # ok-uuid should succeed; error-uuid won't appear (exception path)
        statuses = json.loads((tmp_path / "fetch_status.json").read_text(encoding="utf-8"))
        assert statuses["ok-uuid"] == "success"
        # error-uuid might not be in statuses (exception loses the uuid mapping)
        # This documents the known limitation from research.md section 5.2


# ---------------------------------------------------------------------------
# Helpers for httpx sweep tests
# ---------------------------------------------------------------------------

def _fake_listing(uuid, name="Test Attorney"):
    return {
        "uuid": uuid,
        "name": name,
        "profile_url": f"https://profiles.superlawyers.com/california/la/lawyer/test/{uuid}.html",
    }


def _write_listings(tmp_path, listings):
    path = tmp_path / "listings.json"
    path.write_text(json.dumps(listings), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# httpx fetch-one tests
# ---------------------------------------------------------------------------

class TestHttpxFetchOne:
    @pytest.mark.asyncio
    async def test_returns_html_on_success(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>profile content</body></html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "success"
        assert os.path.exists(os.path.join(html_dir, "uuid-1.html"))

    @pytest.mark.asyncio
    async def test_returns_cf_blocked_on_challenge(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><title>Just a moment...</title></html>"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "cf_blocked"
        assert not os.path.exists(os.path.join(html_dir, "uuid-1.html"))

    @pytest.mark.asyncio
    async def test_returns_failed_on_404(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "failed"

    @pytest.mark.asyncio
    async def test_returns_cf_blocked_on_exception(self, tmp_path):
        from commands.fetch_profiles import _httpx_fetch_one

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection error"))

        uuid, status = await _httpx_fetch_one(
            mock_client, "uuid-1", _fake_listing("uuid-1"), html_dir,
        )
        assert status == "cf_blocked"


# ---------------------------------------------------------------------------
# httpx sweep tests
# ---------------------------------------------------------------------------

class TestHttpxSweep:
    @pytest.mark.asyncio
    async def test_sweep_returns_statuses(self, tmp_path):
        from commands.fetch_profiles import _httpx_sweep

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)
        to_fetch = {"uuid-1": _fake_listing("uuid-1")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html>profile</html>"
        mock_response.headers = {}

        with patch("commands.fetch_profiles.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            statuses, cf_blocked = await _httpx_sweep(
                to_fetch, html_dir, delay_min=0.0, delay_max=0.0,
            )

        assert statuses["uuid-1"] == "success"
        assert len(cf_blocked) == 0

    @pytest.mark.asyncio
    async def test_sweep_collects_cf_blocked(self, tmp_path):
        from commands.fetch_profiles import _httpx_sweep

        html_dir = str(tmp_path / "html")
        os.makedirs(html_dir)
        to_fetch = {"uuid-cf": _fake_listing("uuid-cf")}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><title>Just a moment...</title></html>"
        mock_response.headers = {}

        with patch("commands.fetch_profiles.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client

            statuses, cf_blocked = await _httpx_sweep(
                to_fetch, html_dir, delay_min=0.0, delay_max=0.0,
            )

        assert "uuid-cf" not in statuses
        assert "uuid-cf" in cf_blocked


# ---------------------------------------------------------------------------
# _write_status tests
# ---------------------------------------------------------------------------

class TestWriteStatus:
    def test_writes_json(self, tmp_path):
        from commands.fetch_profiles import _write_status

        path = str(tmp_path / "status.json")
        _write_status(path, {"uuid-1": "success"})

        with open(path) as f:
            data = json.load(f)
        assert data == {"uuid-1": "success"}


# ---------------------------------------------------------------------------
# run() signature / two-phase tests
# ---------------------------------------------------------------------------

class TestRunSignature:
    @pytest.mark.asyncio
    async def test_run_accepts_new_params(self, tmp_path):
        """run() should accept browsers, delay, page_wait, no_httpx params."""
        listings = {"uuid-1": _fake_listing("uuid-1")}
        listings_path = _write_listings(tmp_path, listings)
        (tmp_path / "html").mkdir()

        mock_sweep = AsyncMock(return_value=({"uuid-1": "success"}, {}))

        with patch("commands.fetch_profiles._httpx_sweep", mock_sweep), \
             patch("commands.fetch_profiles.ScraperPool"), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            from commands.fetch_profiles import run
            result = await run(
                listings_path,
                browsers=2,
                delay=(1.0, 2.0),
                page_wait=0.5,
                no_httpx=False,
            )

        assert result == str(tmp_path)

    @pytest.mark.asyncio
    async def test_no_httpx_skips_sweep(self, tmp_path):
        """no_httpx=True should skip httpx sweep and go straight to browser."""
        listings = {"uuid-1": _fake_listing("uuid-1")}
        listings_path = _write_listings(tmp_path, listings)
        (tmp_path / "html").mkdir()

        mock_sweep = AsyncMock()

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value="<html>ok</html>")
        mock_pool.__aenter__ = AsyncMock(return_value=mock_pool)
        mock_pool.__aexit__ = AsyncMock(return_value=False)

        with patch("commands.fetch_profiles._httpx_sweep", mock_sweep), \
             patch("commands.fetch_profiles.ScraperPool", return_value=mock_pool), \
             patch("commands.fetch_profiles.is_progress_enabled", return_value=False):
            from commands.fetch_profiles import run
            await run(listings_path, no_httpx=True)

        mock_sweep.assert_not_awaited()
