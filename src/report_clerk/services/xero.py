"""Xero web-app reports: navigation on the shared browser session, one class per Xero report, and the service."""
import datetime as dt
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel

from report_clerk.config import XeroConfig
from report_clerk.services import browser
from report_clerk.services.browser import LONG, BrowserError, read_rows


def xdate(d: dt.date) -> str:
    # Xero's date-field format, e.g. '1 Jul 2024'.
    return f"{d.day} {d:%b %Y}"


def _home_ready(page) -> None:
    page.get_by_role("combobox", name="Find a report").wait_for(timeout=60_000)


@dataclass
class XeroSession:
    browser: browser.Session
    base: str           # https://reporting.xero.com/!<org_short_code>

    @property
    def page(self):
        return self.browser.page

    def goto_home(self) -> None:
        self.page.goto(f"{self.base}/home")
        _home_ready(self.page)

    def open_report_by_id(self, run_id: int) -> None:
        self.page.goto(f"{self.base}/v1/Run/{run_id}")

    def open_report_by_name(self, name: str) -> None:
        self.goto_home()
        self.page.get_by_role("link", name=name, exact=True).first.click()

    def run_report(self) -> None:
        self.page.get_by_role("button", name="Update", exact=True).click()
        self.page.wait_for_load_state("networkidle", timeout=LONG)

    def export_excel(self, filename: str) -> Path:
        self.page.get_by_role("button", name="Export", exact=True).click()
        excel = self.page.get_by_role("button", name="Excel", exact=True)
        excel.wait_for(state="visible", timeout=10_000)
        return self.browser.download(excel.click, filename)


@contextmanager
def session(config: XeroConfig) -> Iterator[XeroSession]:
    base = f"https://reporting.xero.com/!{config.org_short_code}"
    site = browser.Site(name="Xero", start_url=f"{base}/home", login_url=re.compile(r"login\.xero\.com"), ready=_home_ready)
    with browser.session(config, site) as shared:
        yield XeroSession(shared, base)


class Transaction(BaseModel):
    account: str
    date: date
    source: str
    description: str
    reference: str
    currency: str
    debit: Decimal      # AUD
    credit: Decimal     # AUD

    @property
    def amount(self) -> Decimal:
        return self.debit - self.credit


def long_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{1,2} \w{3,9} \d{4}", value.strip()):
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
    return None


def _decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal(0)
    return Decimal(str(value).replace(",", ""))


class AccountTransactions:
    # Ticked accounts over a date range; the export has one section per account.
    RUN_ID = 1009
    HEADER = ("Date", "Source", "Description", "Reference", "Currency",
              "Debit (Source)", "Credit (Source)", "Debit (AUD)", "Credit (AUD)", "Running Balance (AUD)")

    @staticmethod
    def export(xero: XeroSession, accounts: list[str], start: date, end: date) -> list[tuple]:
        page = xero.page
        xero.open_report_by_id(AccountTransactions.RUN_ID)
        search = page.get_by_role("combobox", name="Search for accounts")
        search.wait_for(timeout=60_000)
        for label in accounts:
            search.click()
            search.fill(label.split(" - ")[0] + " -")
            option = page.get_by_text(label, exact=True)
            option.wait_for(timeout=10_000)
            option.click()
        page.keyboard.press("Escape")

        summary = page.get_by_text(re.compile(r"^\d+ accounts? selected$"))
        selected = int(re.match(r"\d+", summary.first.inner_text()).group()) if summary.count() else 1
        if selected != len(accounts):
            raise BrowserError(f"selected {selected} accounts, expected {len(accounts)}")

        end_box = page.get_by_role("textbox", name="Select end date")
        start_box = end_box.locator("xpath=preceding::input[1]")
        for box, value in ((start_box, start), (end_box, end)):
            box.click(click_count=3)
            box.fill(xdate(value))
        page.keyboard.press("Tab")
        xero.run_report()
        return read_rows(xero.export_excel(f"account-transactions-{start:%Y%m%d}-{end:%Y%m%d}.xlsx"))

    @staticmethod
    def parse(rows: list[tuple], accounts: list[str], start: date, end: date) -> list[Transaction]:
        # Layout: title lines, "For the period ...", header row, then per account: name row, dated rows, total.
        header = next((i for i, r in enumerate(rows) if r and r[0] == "Date"), None)
        if header is None or tuple(rows[header][:10]) != AccountTransactions.HEADER:
            raise ValueError(f"unexpected Account Transactions header: {rows[header][:10] if header is not None else None}")
        want = f"For the period {long_date(start)} to {long_date(end)}"
        got = next((r[0] for r in rows if r and isinstance(r[0], str) and r[0].startswith("For the period")), None)
        if got != want:
            raise ValueError(f"Account Transactions period mismatch: {got!r} != {want!r}")

        sections = {}
        for label in accounts:
            sections[label] = label
            sections[label.split(" - ", 1)[-1]] = label
        transactions, account = [], None
        for r in rows[header + 1:]:
            if not r or r[0] is None:
                continue
            if isinstance(r[0], str) and r[0].strip() in sections and all(c in (None, "") for c in r[1:10]):
                account = sections[r[0].strip()]
                continue
            day = _date(r[0])
            if day is None or not r[1]:
                continue  # totals, opening and closing balances
            if account is None:
                raise ValueError(f"transaction on {day} before any account section")
            if not start <= day <= end:
                raise ValueError(f"transaction on {day} outside {start} to {end}")
            transactions.append(Transaction(
                account=account, date=day, source=str(r[1]), description=str(r[2] or ""), reference=str(r[3] or ""),
                currency=str(r[4] or ""), debit=_decimal(r[7]), credit=_decimal(r[8])))
        return transactions


class ExecutiveSummary:
    # One month ending on a given date.
    @staticmethod
    def export(xero: XeroSession, month_end: date) -> list[tuple]:
        xero.open_report_by_name("Executive Summary")
        box = xero.page.get_by_role("textbox").first  # the "month ending" date field; not yet verified on the live page
        box.wait_for(timeout=60_000)
        box.click(click_count=3)
        box.fill(xdate(month_end))
        xero.page.keyboard.press("Tab")
        xero.run_report()
        return read_rows(xero.export_excel(f"executive-summary-{month_end:%Y%m}.xlsx"))

    @staticmethod
    def parse(rows: list[tuple], month_end: date) -> dict[str, Decimal]:
        # Label -> first number on its row, which is the selected month's column.
        head = " ".join(str(c) for r in rows[:6] for c in r if c)
        if "Executive Summary" not in head:
            raise ValueError("not an Executive Summary export")
        if f"{month_end:%B %Y}" not in head:
            raise ValueError(f"month {month_end:%B %Y} not in Executive Summary header")
        values = {}
        for r in rows:
            if r and isinstance(r[0], str):
                number = next((c for c in r[1:] if isinstance(c, (int, float)) and not isinstance(c, bool)), None)
                if number is not None:
                    values.setdefault(r[0].strip(), Decimal(str(number)))
        return values


class XeroService:
    # Reports exported from the Xero web app. Local only: may need an interactive login.
    def __init__(self, config: XeroConfig):
        self.config = config

    def _account_transactions_rows(self, accounts: list[str], start: date, end: date) -> list[tuple]:
        with session(self.config) as xero:
            return AccountTransactions.export(xero, accounts, start, end)

    def _executive_summary_rows(self, month_ends: list[date]) -> dict[date, list[tuple]]:
        with session(self.config) as xero:
            return {m: ExecutiveSummary.export(xero, m) for m in month_ends}

    def account_transactions(self, accounts: list[str], start: date, end: date) -> list[Transaction]:
        return AccountTransactions.parse(self._account_transactions_rows(accounts, start, end), accounts, start, end)

    def executive_summaries(self, month_ends: list[date]) -> dict[date, dict[str, Decimal]]:
        return {m: ExecutiveSummary.parse(rows, m) for m, rows in self._executive_summary_rows(month_ends).items()}
