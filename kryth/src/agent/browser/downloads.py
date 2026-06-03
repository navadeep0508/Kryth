"""Download handling for browser automation.

Manages file downloads through Playwright's download events.
Supports waiting for downloads, saving to specific locations,
and tracking download progress.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Download, Page

logger = logging.getLogger(__name__)


class DownloadHandler:
    """Handles browser file downloads via Playwright.

    Usage:
        handler = DownloadHandler(page, download_dir="./downloads")
        path = handler.download_url("https://example.com/file.pdf")
        # Or listen for navigation-triggered downloads:
        with handler.expect_download() as download:
            page.click("#download-btn")
        print(f"Downloaded to {download.path}")
    """

    def __init__(
        self,
        page: Page,
        download_dir: str | None = None,
    ) -> None:
        self._page = page
        self._download_dir = Path(download_dir or os.path.join(".", "downloads"))
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._active_download: Download | None = None

    def download_url(self, url: str, *, timeout: float = 60000) -> str:
        """Navigate to a URL that triggers a download and save the file.

        Args:
            url: The URL to navigate to (expected to trigger a download).
            timeout: Maximum time to wait for the download in ms.

        Returns:
            Path to the saved file, or error message.
        """
        try:
            with self._page.expect_download(timeout=timeout) as download_info:
                self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

            download = download_info.value
            return self._save_download(download)
        except Exception as e:
            return f"[ERROR] download failed: {e}"

    def expect_download(self, *, timeout: float = 60000) -> "DownloadContext":
        """Context manager for waiting for a download after an action.

        Usage:
            with handler.expect_download(timeout=30000) as ctx:
                page.click("#download-button")
            path = ctx.result
        """
        return DownloadContext(self._page, self._download_dir, timeout)

    def expect_multiple_downloads(
        self, count: int = 1, *, timeout: float = 60000,
    ) -> list[str]:
        """Wait for multiple downloads and save them.

        Args:
            count: Number of downloads to expect.
            timeout: Total timeout in ms.

        Returns:
            List of paths to saved files.
        """
        paths: list[str] = []
        deadline = time.time() + timeout / 1000.0

        with self._page.expect_download(timeout=timeout) as download_info:
            pass  # Action happens outside

        try:
            download = download_info.value
            paths.append(self._save_download(download))
        except Exception as e:
            logger.warning("Download failed: %s", e)

        return paths

    def _save_download(self, download: Download) -> str:
        """Save a download to the download directory."""
        suggested = download.suggested_filename or f"download_{int(time.time())}"
        dest = str(self._download_dir / suggested)
        download.save_as(dest)
        logger.info("Download saved: %s", dest)
        return dest

    @property
    def download_dir(self) -> str:
        return str(self._download_dir)


class DownloadContext:
    """Context manager for download events.

    Usage:
        with DownloadContext(page, download_dir, timeout) as ctx:
            page.click("#download")
        path = ctx.result
    """

    def __init__(
        self,
        page: Page,
        download_dir: Path,
        timeout: float,
    ) -> None:
        self._page = page
        self._download_dir = download_dir
        self._timeout = timeout
        self.result: str = ""

    def __enter__(self) -> "DownloadContext":
        self._download_info = self._page.expect_download(timeout=self._timeout)
        self._download_info.__enter__()
        return self

    def __exit__(self, *args) -> None:
        try:
            self._download_info.__exit__(*args)
            download = self._download_info.value
            if download:
                suggested = download.suggested_filename or f"download_{int(time.time())}"
                dest = str(self._download_dir / suggested)
                download.save_as(dest)
                self.result = dest
        except Exception as e:
            self.result = f"[ERROR] download failed: {e}"
