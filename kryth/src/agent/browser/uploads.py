"""Upload handling for browser automation.

Manages file uploads through Playwright's file chooser events.
Supports single and multiple file uploads, drag-and-drop, and
file input detection.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from playwright.sync_api import FileChooser, Page

logger = logging.getLogger(__name__)


class UploadHandler:
    """Handles browser file uploads via Playwright.

    Usage:
        handler = UploadHandler(page)
        handler.upload_file("input[type='file']", "/path/to/file.pdf")
        handler.upload_multiple("input[multiple]", ["/path/to/a.pdf", "/path/to/b.pdf"])
    """

    def __init__(self, page: Page) -> None:
        self._page = page

    def upload_file(
        self,
        selector: str,
        file_path: str,
        *,
        timeout: float = 30000,
    ) -> str:
        """Upload a single file by setting the file input value.

        Works with <input type="file"> elements.

        Args:
            selector: CSS selector for the file input element.
            file_path: Absolute or relative path to the file to upload.
            timeout: Maximum time to wait for the element in ms.

        Returns:
            Status message.
        """
        if not os.path.exists(file_path):
            return f"[ERROR] file not found: {file_path}"

        abs_path = os.path.abspath(file_path)

        try:
            input_el = self._page.wait_for_selector(selector, timeout=timeout)
            if not input_el:
                return f"[ERROR] upload element not found: {selector}"

            input_el.set_input_files(abs_path)
            logger.info("Uploaded file: %s", abs_path)
            return f"uploaded {os.path.basename(file_path)}"
        except Exception as e:
            return f"[ERROR] upload failed: {e}"

    def upload_multiple(
        self,
        selector: str,
        file_paths: list[str],
        *,
        timeout: float = 30000,
    ) -> str:
        """Upload multiple files at once.

        The input element must have the ``multiple`` attribute.

        Args:
            selector: CSS selector for the file input element.
            file_paths: List of file paths to upload.
            timeout: Maximum time to wait for the element in ms.

        Returns:
            Status message.
        """
        abs_paths = []
        for fp in file_paths:
            if not os.path.exists(fp):
                return f"[ERROR] file not found: {fp}"
            abs_paths.append(os.path.abspath(fp))

        try:
            input_el = self._page.wait_for_selector(selector, timeout=timeout)
            if not input_el:
                return f"[ERROR] upload element not found: {selector}"

            input_el.set_input_files(abs_paths)
            names = ", ".join(os.path.basename(p) for p in file_paths)
            logger.info("Uploaded files: %s", names)
            return f"uploaded {len(file_paths)} file(s): {names}"
        except Exception as e:
            return f"[ERROR] upload_multiple failed: {e}"

    def upload_via_filechooser(
        self,
        file_path: str,
        *,
        timeout: float = 30000,
    ) -> str:
        """Upload a file by intercepting the file chooser event.

        Use this when the upload is triggered by a click on a non-input
        element (e.g., a styled button or drag-and-drop zone).

        Usage:
            handler.upload_via_filechooser("/path/to/file.pdf")
            page.click(".upload-button")

        Args:
            file_path: Path to the file to upload.
            timeout: Maximum time to wait for the file chooser in ms.

        Returns:
            A context manager that should be used before the trigger action.
        """
        return FileChooserContext(self._page, [file_path], timeout)

    def upload_multiple_via_filechooser(
        self,
        file_paths: list[str],
        *,
        timeout: float = 30000,
    ) -> str:
        """Upload multiple files by intercepting the file chooser."""
        return FileChooserContext(self._page, file_paths, timeout)

    def file_input_exists(self) -> bool:
        """Check if the page has any file input elements."""
        return len(self._page.query_selector_all("input[type='file']")) > 0


class FileChooserContext:
    """Context manager for file chooser uploads.

    Usage:
        with FileChooserContext(page, ["file.pdf"], timeout=30000) as ctx:
            page.click(".upload-button")
        result = ctx.result
    """

    def __init__(
        self,
        page: Page,
        file_paths: list[str],
        timeout: float,
    ) -> None:
        abs_paths = []
        for fp in file_paths:
            if not os.path.exists(fp):
                raise FileNotFoundError(f"File not found: {fp}")
            abs_paths.append(os.path.abspath(fp))

        self._page = page
        self._file_paths = abs_paths
        self._timeout = timeout
        self.result: str = ""

    def __enter__(self) -> "FileChooserContext":
        self._chooser_ctx = self._page.expect_file_chooser(timeout=self._timeout)
        self._chooser_ctx.__enter__()
        return self

    def __exit__(self, *args) -> None:
        try:
            self._chooser_ctx.__exit__(*args)
            file_chooser: FileChooser = self._chooser_ctx.value
            if file_chooser:
                file_chooser.set_files(self._file_paths)
                names = [os.path.basename(p) for p in self._file_paths]
                self.result = f"uploaded {len(names)} file(s): {', '.join(names)}"
                logger.info("Uploaded via file chooser: %s", names)
        except Exception as e:
            self.result = f"[ERROR] upload failed: {e}"
