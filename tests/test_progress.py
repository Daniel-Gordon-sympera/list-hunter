# tests/test_progress.py
"""Tests for progress bar classes."""

import os
from unittest.mock import patch

from progress import CrawlProgress, FetchProgress, is_progress_enabled


class TestIsProgressEnabled:
    def test_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove the env var if present
            os.environ.pop("SUPERLAWYERS_NO_PROGRESS", None)
            assert is_progress_enabled() is True

    def test_disabled_by_env_var(self):
        with patch.dict(os.environ, {"SUPERLAWYERS_NO_PROGRESS": "1"}):
            assert is_progress_enabled() is False


class TestCrawlProgress:
    def test_context_manager(self):
        with CrawlProgress(total_pas=10) as cp:
            assert cp is not None

    def test_pa_page_fetched_updates_state(self):
        cp = CrawlProgress(total_pas=5)
        cp.start()
        cp.pa_page_fetched(pa_slug="family-law", page=2, new_count=10)
        assert cp._active_workers["family-law"] == 2
        assert cp._unique_count == 10
        cp.stop()

    def test_pa_completed_increments(self):
        cp = CrawlProgress(total_pas=5)
        cp.start()
        cp._active_workers["family-law"] = 3
        cp.pa_completed("family-law")
        assert cp._completed_pas == 1
        assert "family-law" not in cp._active_workers
        cp.stop()


class TestFetchProgress:
    def test_context_manager(self):
        with FetchProgress(total=100) as fp:
            assert fp is not None

    def test_advance_does_not_error(self):
        fp = FetchProgress(total=10)
        fp.start()
        fp.advance()
        fp.advance(2)
        fp.stop()

    def test_without_rich_no_error(self):
        """Calling methods when progress is not started should not raise."""
        fp = FetchProgress(total=10)
        fp.advance()  # should silently no-op
        fp.stop()     # should silently no-op
