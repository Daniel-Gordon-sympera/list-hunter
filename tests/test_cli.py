# tests/test_cli.py
"""Tests for the CLI entry point (cli.py)."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli import cmd_crawl_listings, cmd_discover, cmd_export, cmd_fetch_profiles, cmd_parse_profiles, main, setup_logging


# ---------------------------------------------------------------------------
# setup_logging tests
# ---------------------------------------------------------------------------


class TestSetupLogging:
    def test_verbose_sets_debug(self):
        setup_logging(verbose=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_non_verbose_sets_info(self):
        setup_logging(verbose=False)
        assert logging.getLogger().level == logging.INFO


# ---------------------------------------------------------------------------
# --help output tests
# ---------------------------------------------------------------------------


class TestHelpOutput:
    def test_main_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["cli.py", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "discover" in captured.out
        assert "crawl-listings" in captured.out
        assert "fetch-profiles" in captured.out
        assert "parse-profiles" in captured.out
        assert "export" in captured.out

    def test_discover_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["cli.py", "discover", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "location" in captured.out

    def test_export_help_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["cli.py", "export", "--help"]):
                main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "input" in captured.out
        assert "--output" in captured.out or "-o" in captured.out


# ---------------------------------------------------------------------------
# Bad arguments
# ---------------------------------------------------------------------------


class TestBadArguments:
    def test_no_subcommand_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["cli.py"]):
                main()
        assert exc_info.value.code != 0

    def test_unknown_subcommand_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["cli.py", "nonexistent"]):
                main()
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Subcommand wiring tests (mock the command modules)
# ---------------------------------------------------------------------------


class TestSubcommandWiring:
    def test_cmd_discover_calls_discover_run(self):
        mock_run = AsyncMock(return_value="/data/practice_areas.json")
        args = MagicMock()
        args.location = "Los Angeles, CA"

        with patch("commands.discover.run", mock_run):
            cmd_discover(args)
            mock_run.assert_awaited_once_with("Los Angeles, CA")

    def test_cmd_crawl_listings_calls_crawl_run(self):
        mock_run = AsyncMock(return_value="/data/listings.json")
        args = MagicMock()
        args.input = "/data/practice_areas.json"

        with patch("commands.crawl_listings.run", mock_run):
            cmd_crawl_listings(args)
            mock_run.assert_awaited_once_with("/data/practice_areas.json")

    def test_cmd_fetch_profiles_calls_fetch_run(self):
        mock_run = AsyncMock(return_value="/data/html")
        args = MagicMock()
        args.input = "/data/listings.json"
        args.force = False
        args.retry_cf = False

        with patch("commands.fetch_profiles.run", mock_run):
            cmd_fetch_profiles(args)
            mock_run.assert_awaited_once_with("/data/listings.json", force=False, retry_cf=False)

    def test_cmd_parse_profiles_calls_parse_run(self):
        mock_run = MagicMock(return_value="/data/records.json")
        args = MagicMock()
        args.data_dir = "/data/html"

        with patch("commands.parse_profiles.run", mock_run):
            cmd_parse_profiles(args)
            mock_run.assert_called_once_with("/data/html")

    def test_cmd_export_calls_export_run(self):
        mock_run = MagicMock(return_value="/output/superlawyers.csv")
        args = MagicMock()
        args.input = "/data/records.json"
        args.output = "/output"

        with patch("commands.export.run", mock_run):
            cmd_export(args)
            mock_run.assert_called_once_with("/data/records.json", "/output")

    def test_cmd_export_output_defaults_to_none(self):
        mock_run = MagicMock(return_value="/output/superlawyers.csv")
        args = MagicMock()
        args.input = "/data/records.json"
        args.output = None

        with patch("commands.export.run", mock_run):
            cmd_export(args)
            mock_run.assert_called_once_with("/data/records.json", None)


# ---------------------------------------------------------------------------
# Integration: main() dispatches to the correct subcommand function
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def test_main_discover_dispatches(self):
        with patch("sys.argv", ["cli.py", "discover", "Los Angeles, CA"]), \
             patch("cli.cmd_discover") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.location == "Los Angeles, CA"

    def test_main_crawl_listings_dispatches(self):
        with patch("sys.argv", ["cli.py", "crawl-listings", "/path/to/pa.json"]), \
             patch("cli.cmd_crawl_listings") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.input == "/path/to/pa.json"

    def test_main_fetch_profiles_dispatches(self):
        with patch("sys.argv", ["cli.py", "fetch-profiles", "/path/to/listings.json"]), \
             patch("cli.cmd_fetch_profiles") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.input == "/path/to/listings.json"

    def test_main_parse_profiles_dispatches(self):
        with patch("sys.argv", ["cli.py", "parse-profiles", "/path/to/data"]), \
             patch("cli.cmd_parse_profiles") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.data_dir == "/path/to/data"

    def test_main_export_dispatches(self):
        with patch("sys.argv", ["cli.py", "export", "/path/to/records.json", "-o", "/output"]), \
             patch("cli.cmd_export") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.input == "/path/to/records.json"
            assert args.output == "/output"

    def test_main_export_without_output(self):
        with patch("sys.argv", ["cli.py", "export", "/path/to/records.json"]), \
             patch("cli.cmd_export") as mock_cmd:
            main()
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args[0][0]
            assert args.input == "/path/to/records.json"
            assert args.output is None

    def test_main_verbose_flag(self):
        with patch("sys.argv", ["cli.py", "-v", "discover", "LA, CA"]), \
             patch("cli.cmd_discover"), \
             patch("cli.setup_logging") as mock_logging:
            main()
            mock_logging.assert_called_once_with(verbose=True)
