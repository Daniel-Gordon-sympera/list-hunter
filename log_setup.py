# log_setup.py
"""Centralized logging configuration for the Super Lawyers scraper.

Replaces the inline setup_logging() from cli.py with a module that
supports both console and file logging. File logs are written to
``data/{city}_{st}/logs/{command}_{YYYYMMDD_HHMMSS}.log`` and always
capture DEBUG-level messages for post-mortem analysis.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(
    verbose: bool = False,
    data_dir: str | None = None,
    command_name: str = "run",
    use_rich: bool = False,
) -> str | None:
    """Configure the root logger with console and optional file handlers.

    Args:
        verbose: If True, console handler uses DEBUG level; otherwise INFO.
        data_dir: If provided, a file handler is added that writes DEBUG-level
            logs to ``{data_dir}/logs/{command_name}_{timestamp}.log``.
        command_name: Label used in the log filename (e.g. "crawl-listings").
        use_rich: If True, use Rich library's RichHandler for console output.

    Returns:
        Path to the log file if file logging was set up, else None.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    console_level = logging.DEBUG if verbose else logging.INFO

    # Console handler
    if use_rich:
        try:
            from rich.logging import RichHandler

            console_handler = RichHandler(
                level=console_level,
                show_time=True,
                show_path=False,
                markup=False,
            )
        except ImportError:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(console_level)
            console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    else:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root.addHandler(console_handler)

    # File handler (when data_dir is provided)
    log_path = None
    if data_dir:
        log_dir = os.path.join(data_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"{command_name}_{timestamp}.log"
        log_path = os.path.join(log_dir, log_filename)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)

    return log_path
