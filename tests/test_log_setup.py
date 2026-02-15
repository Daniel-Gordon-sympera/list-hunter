# tests/test_log_setup.py
"""Tests for log_setup module."""

import logging
import os

from log_setup import setup_logging


class TestConsoleOnly:
    def test_default_level_is_info(self):
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG  # root is DEBUG
        # Console handler should be INFO
        assert len(root.handlers) == 1
        assert root.handlers[0].level == logging.INFO

    def test_verbose_sets_console_to_debug(self):
        setup_logging(verbose=True)
        root = logging.getLogger()
        assert root.handlers[0].level == logging.DEBUG

    def test_no_file_handler_without_data_dir(self):
        result = setup_logging()
        root = logging.getLogger()
        assert result is None
        assert len(root.handlers) == 1

    def test_clears_previous_handlers(self):
        setup_logging()
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) == 1


class TestFileLogging:
    def test_file_handler_created(self, tmp_path):
        log_path = setup_logging(data_dir=str(tmp_path), command_name="test")
        root = logging.getLogger()
        assert log_path is not None
        assert os.path.exists(log_path)
        assert len(root.handlers) == 2  # console + file

    def test_log_file_in_logs_subdir(self, tmp_path):
        log_path = setup_logging(data_dir=str(tmp_path), command_name="crawl")
        assert "/logs/" in log_path or "\\logs\\" in log_path
        assert "crawl_" in os.path.basename(log_path)
        assert log_path.endswith(".log")

    def test_file_handler_captures_debug(self, tmp_path):
        log_path = setup_logging(data_dir=str(tmp_path), command_name="test")
        root = logging.getLogger()
        file_handler = root.handlers[1]
        assert file_handler.level == logging.DEBUG

    def test_log_message_written_to_file(self, tmp_path):
        log_path = setup_logging(data_dir=str(tmp_path), command_name="test")
        test_logger = logging.getLogger("test_write")
        test_logger.info("hello from test")
        # Flush handlers
        for h in logging.getLogger().handlers:
            h.flush()
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        assert "hello from test" in content

    def test_returns_log_path(self, tmp_path):
        result = setup_logging(data_dir=str(tmp_path), command_name="export")
        assert result is not None
        assert "export_" in result

    def test_creates_logs_directory(self, tmp_path):
        data_dir = str(tmp_path / "new_data")
        os.makedirs(data_dir)
        setup_logging(data_dir=data_dir, command_name="test")
        assert os.path.isdir(os.path.join(data_dir, "logs"))

    def test_repeated_calls_dont_accumulate_handlers(self, tmp_path):
        setup_logging(data_dir=str(tmp_path), command_name="a")
        setup_logging(data_dir=str(tmp_path), command_name="b")
        root = logging.getLogger()
        assert len(root.handlers) == 2  # still just console + file
