"""
NR-AI Browser Driver Abstraction (Step 5 Phase 2).

Manages the Playwright Chromium lifecycle with deterministic safety gates,
isolated browser contexts, tab tracking, and safe defaults.
"""

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Union
import uuid

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from app.agent.browser_safety import (
    BrowserSafetyError,
    BrowserSafetyGate,
    DangerousDownloadError,
)

logger = logging.getLogger("NRAI.BrowserDriver")


@dataclass
class BrowserTabInfo:
    """Metadata describing an active browser tab."""
    tab_id: str
    title: str
    url: str
    active: bool
    created_at: float = field(default_factory=time.time)


@dataclass
class DownloadRecord:
    """Audit record for a downloaded file."""
    url: str
    suggested_filename: str
    quarantine_path: str
    timestamp: float = field(default_factory=time.time)
    size_bytes: Optional[int] = None
    safe: bool = True
    rejection_reason: Optional[str] = None


class BrowserDriver:
    """
    Deterministic Browser Driver wrapping Playwright Chromium.

    Enforces:
    - Safe startup and isolated context creation
    - Mandatory safety gate inspection on navigation
    - Multi-tab tracking and active tab focus
    - Download interception and quarantine
    - Resource cleanup on shutdown
    """

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 15000,
        safety_gate: Optional[BrowserSafetyGate] = None,
        downloads_dir: Optional[str] = None,
    ):
        self.headless = headless
        self.default_timeout_ms = timeout_ms
        self.safety_gate = safety_gate or BrowserSafetyGate(quarantine_dir=downloads_dir)
        self.downloads_dir = Path(downloads_dir or "C:/NR-AI/scratch/downloads").resolve()
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        # Lifecycle handles
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._pages: Dict[str, Page] = {}
        self._active_tab_id: Optional[str] = None

        # Session metadata
        self.session_id: str = str(uuid.uuid4())
        self.created_at: float = time.time()
        self.last_activity: float = time.time()
        self.download_records: List[DownloadRecord] = []

    # -------------------------------------------------------------------------
    # Context Manager
    # -------------------------------------------------------------------------

    def __enter__(self) -> "BrowserDriver":
        self.launch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -------------------------------------------------------------------------
    # Lifecycle: Launch & Shutdown
    # -------------------------------------------------------------------------

    def launch(self) -> None:
        """Starts Playwright, launches Chromium, and establishes initial context."""
        if self._browser and self._browser.is_connected():
            return

        logger.info(f"Launching Playwright Chromium (session: {self.session_id}, headless: {self.headless})")
        self._playwright = sync_playwright().start()

        # Launch Chromium with safe default arguments
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
            ],
        )

        self.create_context()
        self.last_activity = time.time()

    def create_context(self) -> BrowserContext:
        """Creates an isolated browser context with download interception."""
        if not self._browser:
            self.launch()

        self._context = self._browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
            user_agent="NR-AI-SafeBrowser/1.0 (Windows NT 10.0; Win64; x64)",
        )
        self._context.set_default_timeout(self.default_timeout_ms)

        # Wire download interception
        self._context.on("page", self._on_page_created)
        return self._context

    def _on_page_created(self, page: Page) -> None:
        """Registers download listener on new pages."""
        page.on("download", self._handle_download)

    def _handle_download(self, download) -> None:
        """Intercepts download and routes strictly to the quarantine directory."""
        suggested_name = download.suggested_filename
        url = download.url
        logger.info(f"Intercepted download: '{suggested_name}' from {url}")

        is_safe, reason = self.safety_gate.validate_download(suggested_name)
        if not is_safe:
            logger.warning(f"Blocking dangerous download '{suggested_name}': {reason}")
            try:
                download.cancel()
            except Exception:
                pass
            record = DownloadRecord(
                url=url,
                suggested_filename=suggested_name,
                quarantine_path="",
                safe=False,
                rejection_reason=reason,
            )
            self.download_records.append(record)
            raise DangerousDownloadError(f"Blocked dangerous download '{suggested_name}': {reason}")

        # Save to quarantine directory
        dest_path = self.safety_gate.get_quarantine_path(suggested_name)
        try:
            download.save_as(str(dest_path))
            size = dest_path.stat().st_size if dest_path.exists() else 0
            record = DownloadRecord(
                url=url,
                suggested_filename=suggested_name,
                quarantine_path=str(dest_path),
                size_bytes=size,
                safe=True,
            )
            self.download_records.append(record)
            logger.info(f"Download quarantined successfully at: {dest_path}")
        except Exception as e:
            logger.error(f"Failed to quarantine download '{suggested_name}': {e}")

    def close(self) -> None:
        """Clean shutdown of all pages, context, and browser."""
        logger.info(f"Closing BrowserDriver session: {self.session_id}")
        self._pages.clear()
        self._active_tab_id = None

        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def is_running(self) -> bool:
        """Returns True if the browser is active and connected."""
        return bool(self._browser and self._browser.is_connected())

    # -------------------------------------------------------------------------
    # Tab Management
    # -------------------------------------------------------------------------

    def new_page(self) -> Page:
        """Opens a new tab in the active context."""
        if not self._context:
            self.launch()

        page = self._context.new_page()
        page.on("download", self._handle_download)
        tab_id = str(uuid.uuid4())[:8]
        self._pages[tab_id] = page
        self._active_tab_id = tab_id
        self.last_activity = time.time()
        return page

    def get_active_page(self) -> Page:
        """Returns the currently active page/tab, creating one if none exists."""
        if not self._pages or self._active_tab_id not in self._pages:
            return self.new_page()
        return self._pages[self._active_tab_id]

    def list_tabs(self) -> List[BrowserTabInfo]:
        """Lists all open tabs with their active status, URLs, and titles."""
        # Clean up any closed pages
        closed_ids = [tid for tid, p in self._pages.items() if p.is_closed()]
        for tid in closed_ids:
            del self._pages[tid]
            if self._active_tab_id == tid:
                self._active_tab_id = next(iter(self._pages.keys())) if self._pages else None

        tabs = []
        for tid, p in self._pages.items():
            try:
                title = p.title()
                url = p.url
            except Exception:
                title = ""
                url = ""
            tabs.append(BrowserTabInfo(
                tab_id=tid,
                title=title,
                url=url,
                active=(tid == self._active_tab_id),
            ))
        return tabs

    def switch_tab(self, tab_id: Union[str, int]) -> Dict[str, Any]:
        """Switches the active tab."""
        target_id = str(tab_id)
        # Check if passed numeric index
        if target_id.isdigit():
            idx = int(target_id)
            tab_keys = list(self._pages.keys())
            if 0 <= idx < len(tab_keys):
                target_id = tab_keys[idx]

        if target_id not in self._pages:
            raise BrowserSafetyError(f"Tab '{target_id}' not found. Open tabs: {list(self._pages.keys())}")

        page = self._pages[target_id]
        if page.is_closed():
            del self._pages[target_id]
            raise BrowserSafetyError(f"Tab '{target_id}' is closed.")

        page.bring_to_front()
        self._active_tab_id = target_id
        self.last_activity = time.time()
        return {
            "status": "SWITCHED",
            "active_tab_id": target_id,
            "title": page.title(),
            "url": page.url,
        }

    def close_tab(self, tab_id: Optional[Union[str, int]] = None) -> Dict[str, Any]:
        """Closes a specific tab or the active tab."""
        target_id = str(tab_id) if tab_id is not None else self._active_tab_id
        if not target_id or target_id not in self._pages:
            return {"status": "NO_TAB_TO_CLOSE"}

        page = self._pages.pop(target_id)
        if not page.is_closed():
            page.close()

        if self._active_tab_id == target_id:
            self._active_tab_id = next(iter(self._pages.keys())) if self._pages else None

        self.last_activity = time.time()
        return {
            "status": "CLOSED",
            "closed_tab_id": target_id,
            "remaining_tabs": len(self._pages),
            "active_tab_id": self._active_tab_id,
        }

    # -------------------------------------------------------------------------
    # Navigation & Actions
    # -------------------------------------------------------------------------

    def navigate(self, url: str, timeout_ms: Optional[int] = None) -> Dict[str, Any]:
        """
        Navigates active page to URL after passing through the safety gate.
        Raises BrowserSafetyError on disallowed URLs.
        """
        is_safe, reason = self.safety_gate.validate_url(url)
        if not is_safe:
            logger.warning(f"Blocked unsafe navigation to '{url}': {reason}")
            raise BrowserSafetyError(f"Navigation blocked by safety policy: {reason}")

        page = self.get_active_page()
        timeout = timeout_ms or self.default_timeout_ms
        response = page.goto(url, timeout=timeout, wait_until="domcontentloaded")

        self.last_activity = time.time()
        status_code = response.status if response else 200
        return {
            "status": "NAVIGATED",
            "url": page.url,
            "title": page.title(),
            "status_code": status_code,
            "tab_id": self._active_tab_id,
        }

    def back(self) -> Dict[str, Any]:
        """Navigates back in browser history."""
        page = self.get_active_page()
        page.go_back(timeout=self.default_timeout_ms)
        self.last_activity = time.time()
        return {"status": "NAVIGATED_BACK", "url": page.url, "title": page.title()}

    def forward(self) -> Dict[str, Any]:
        """Navigates forward in browser history."""
        page = self.get_active_page()
        page.go_forward(timeout=self.default_timeout_ms)
        self.last_activity = time.time()
        return {"status": "NAVIGATED_FORWARD", "url": page.url, "title": page.title()}

    def refresh(self) -> Dict[str, Any]:
        """Reloads the active page."""
        page = self.get_active_page()
        page.reload(timeout=self.default_timeout_ms)
        self.last_activity = time.time()
        return {"status": "REFRESHED", "url": page.url, "title": page.title()}

    def screenshot(
        self,
        path: Optional[str] = None,
        selector: Optional[str] = None,
    ) -> str:
        """Captures a screenshot of the viewport or specific element selector."""
        page = self.get_active_page()
        if not path:
            screenshots_dir = Path("C:/NR-AI/scratch/screenshots").resolve()
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            path = str(screenshots_dir / f"screenshot_{int(time.time()*1000)}.png")

        dest = Path(path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if selector:
            locator = page.locator(selector)
            locator.screenshot(path=str(dest))
        else:
            page.screenshot(path=str(dest))

        self.last_activity = time.time()
        return str(dest)
