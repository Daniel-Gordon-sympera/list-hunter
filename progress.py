# progress.py
"""Rich progress bar displays for the scraper pipeline.

Provides CrawlProgress (for crawl-listings) and FetchProgress (for
fetch-profiles) that render live progress bars using the Rich library.

Disabled when SUPERLAWYERS_NO_PROGRESS=1 is set (for CI / piped output).
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def is_progress_enabled() -> bool:
    """Return True if progress bars should be shown."""
    if not RICH_AVAILABLE:
        return False
    return not os.environ.get("SUPERLAWYERS_NO_PROGRESS")


class CrawlProgress:
    """Progress display for the crawl-listings phase.

    Shows:
    - Overall PA completion bar
    - Unique attorney count
    - Active worker status
    """

    def __init__(self, total_pas: int) -> None:
        self._total_pas = total_pas
        self._completed_pas = 0
        self._unique_count = 0
        self._active_workers: dict[str, int] = {}  # pa_slug -> current page
        self._progress: Optional[Progress] = None
        self._pa_task_id = None
        self._attorney_task_id = None

    def start(self) -> None:
        if not RICH_AVAILABLE:
            return

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=Console(stderr=True),
        )
        self._pa_task_id = self._progress.add_task(
            "Practice Areas", total=self._total_pas,
        )
        self._attorney_task_id = self._progress.add_task(
            "Attorneys found", total=None,
        )
        self._progress.start()

    def stop(self) -> None:
        if self._progress:
            self._progress.stop()

    def pa_page_fetched(
        self, pa_slug: str, page: int, new_count: int, **kwargs
    ) -> None:
        """Called after each page fetch by a worker."""
        self._active_workers[pa_slug] = page
        self._unique_count += new_count

        if self._progress and self._attorney_task_id is not None:
            active_str = " | ".join(
                f"{slug} (p.{p})" for slug, p in self._active_workers.items()
            )
            self._progress.update(
                self._attorney_task_id,
                description=f"Attorneys: {self._unique_count:,}  Active: {active_str}",
                completed=self._unique_count,
            )

    def pa_completed(self, pa_slug: str) -> None:
        """Called when a PA finishes completely."""
        self._completed_pas += 1
        self._active_workers.pop(pa_slug, None)

        if self._progress and self._pa_task_id is not None:
            self._progress.update(self._pa_task_id, completed=self._completed_pas)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


class FetchProgress:
    """Progress display for the fetch-profiles phase.

    Shows a single progress bar tracking completed/total fetches.
    """

    def __init__(self, total: int) -> None:
        self._total = total
        self._progress: Optional[Progress] = None
        self._task_id = None

    def start(self) -> None:
        if not RICH_AVAILABLE:
            return

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=Console(stderr=True),
        )
        self._task_id = self._progress.add_task(
            "Fetching profiles", total=self._total,
        )
        self._progress.start()

    def stop(self) -> None:
        if self._progress:
            self._progress.stop()

    def advance(self, amount: int = 1) -> None:
        """Advance the progress bar by the given amount."""
        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, advance=amount)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
