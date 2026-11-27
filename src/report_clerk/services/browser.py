"""Playwright session with a persistent profile; opens a visible window only when the site asks for a login."""
import datetime as dt
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import openpyxl

from report_clerk.config import BrowserConfig

LONG = 300_000          # ms; large reports can take minutes to build
LOGIN_WAIT = 15 * 60_000
WINDOWS_BALLOON = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
                   "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                   "$n.ShowBalloonTip(15000, 'report-clerk', '{message}', 'Info'); Start-Sleep 15; $n.Dispose()")


class BrowserError(RuntimeError):
    pass


def notify(message: str, platform: str = sys.platform) -> None:
    # Desktop notification on macOS and Windows; always also printed.
    print(message, file=sys.stderr)
    try:
        if platform == "darwin":
            subprocess.run(["osascript", "-e", f'display notification "{message}" with title "report-clerk" sound name "Glass"'], check=False)
        elif platform == "win32":
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", WINDOWS_BALLOON.format(message=message)])
    except OSError:
        pass


def read_rows(path: Path) -> list[tuple]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [tuple(row) for row in workbook.active.iter_rows(values_only=True)]
    finally:
        workbook.close()


@dataclass
class Site:
    # What the generic session needs to know about a site's login.
    name: str                           # used in messages and screenshot names
    start_url: str
    login_url: re.Pattern               # where the site sends you when a login is needed
    ready: Callable[[object], None]     # waits until the logged-in start page is usable; raises on timeout


@dataclass
class Session:
    page: object
    out_dir: Path

    def download(self, click: Callable[[], None], filename: str) -> Path:
        out = self.out_dir / filename
        with self.page.expect_download(timeout=LONG) as download:
            click()
        download.value.save_as(out)
        if not out.exists() or out.stat().st_size == 0:
            raise BrowserError(f"download missing or empty: {out.name}")
        return out

    def screenshot(self, directory: Path, label: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        shot = directory / f"{label}-error-{dt.datetime.now():%Y%m%d-%H%M%S}.png"
        self.page.screenshot(path=str(shot), full_page=True)
        return shot


def _launch(playwright, profile: str, headless: bool):
    context = playwright.chromium.launch_persistent_context(
        profile, headless=headless, accept_downloads=True, viewport={"width": 1600, "height": 1000})
    return context, (context.pages[0] if context.pages else context.new_page())


def _needs_login(page, site: Site) -> bool:
    page.goto(site.start_url)
    page.wait_for_load_state("domcontentloaded")
    return bool(site.login_url.search(page.url))


def _interactive_login(playwright, profile: str, site: Site) -> None:
    notify(f"{site.name} login needed: log in (and complete 2FA) in the window that just opened")
    context, page = _launch(playwright, profile, headless=False)
    try:
        page.goto(site.start_url)
        origin = "{0.scheme}://{0.netloc}/".format(urlsplit(site.start_url))
        page.wait_for_url(lambda url: url.startswith(origin), timeout=LOGIN_WAIT)  # back on the site after login and 2FA
        site.ready(page)
    finally:
        context.close()  # session cookies persist in the profile


@contextmanager
def session(config: BrowserConfig, site: Site) -> Iterator[Session]:
    from playwright.sync_api import sync_playwright

    profile = str(config.profile_dir.expanduser())
    with tempfile.TemporaryDirectory(prefix="report-clerk-") as temp_dir, sync_playwright() as playwright:
        out_dir = config.download_dir.expanduser() if config.download_dir else Path(temp_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            context, page = _launch(playwright, profile, headless=not config.headed)
            if not _needs_login(page, site):
                break
            context.close()
            if attempt == 2:
                raise BrowserError(f"still not logged in to {site.name} after interactive login")
            _interactive_login(playwright, profile, site)
        browser = Session(page=page, out_dir=out_dir)
        try:
            site.ready(page)
            yield browser
        except Exception:
            browser.screenshot(config.screenshot_dir, site.name.lower())
            raise
        finally:
            context.close()
