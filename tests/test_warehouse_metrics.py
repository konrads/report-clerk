import tomllib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from report_clerk import config
from report_clerk.config import ReportConfig, Settings
from report_clerk.reports import warehouse_metrics as wm
from report_clerk.run import run_all
from report_clerk.services import DataSources
from report_clerk.services.xero import AccountTransactions, ExecutiveSummary, Transaction
from report_clerk.validate import validate

ROOT = Path(__file__).parents[1]
OPTIONS = tomllib.loads((ROOT / "config.toml").read_text())["reports"][-1]["options"]
START, END = date(2026, 7, 1), date(2026, 7, 31)
PERIOD = ("For the period 1 July 2026 to 31 July 2026",)


def export(*body):
    return [("Account Transactions",), PERIOD, (), AccountTransactions.HEADER, *body]


def line(day, source="Receivable Invoice", description="A - B", debit=0.0, credit=0.0):
    return (day, source, description, "R1", "AUD", debit, credit, debit, credit, None)


def txn(account, day, amount, source="Receivable Invoice", description="x"):
    debit, credit = (amount, 0) if amount > 0 else (0, -amount)
    return Transaction(account=account, date=day, source=source, description=description, reference="", currency="AUD",
                       debit=Decimal(str(debit)), credit=Decimal(str(credit)))


def test_parse_maps_sections_by_name_or_label_and_skips_totals():
    rows = export(("Sales Income (B2C)",), line("3 Jul 2026", credit=100.0), ("Total Sales Income (B2C)", None, None),
                  ("310 - Cost of Goods Sold (B2C)",), line(datetime(2026, 7, 4), "Manual Journal", debit=40.5), ("Closing Balance",))
    out = AccountTransactions.parse(rows, ["200 - Sales Income (B2C)", "310 - Cost of Goods Sold (B2C)"], START, END)
    assert [(t.account, t.date, t.amount) for t in out] == [
        ("200 - Sales Income (B2C)", date(2026, 7, 3), Decimal("-100.0")), ("310 - Cost of Goods Sold (B2C)", date(2026, 7, 4), Decimal("40.5"))]


@pytest.mark.parametrize("rows, message", [
    ([PERIOD, ("Date", "Wrong")], "unexpected Account Transactions header"),
    ([("For the period 1 June 2026 to 31 July 2026",), AccountTransactions.HEADER], "period mismatch"),
    (export(line("3 Jul 2026")), "before any account section"),
    (export(("Sales",), line("3 Aug 2026")), "outside"),
])
def test_parse_rejects_unexpected_exports(rows, message):
    with pytest.raises(ValueError, match=message):
        AccountTransactions.parse(rows, ["200 - Sales"], START, END)


def test_executive_summary_takes_first_number_of_each_row():
    rows = [("Executive Summary",), ("For the month ended 31 July 2026",), ("Income",), ("Average value of invoices", "", 2986, 2800)]
    assert ExecutiveSummary.parse(rows, END) == {"Average value of invoices": Decimal("2986")}
    with pytest.raises(ValueError, match="not an Executive Summary"):
        ExecutiveSummary.parse([("Balance Sheet",)], END)
    with pytest.raises(ValueError, match="August 2026"):
        ExecutiveSummary.parse(rows, date(2026, 8, 31))


def test_metrics_apply_order_rent_and_carrier_rules():
    options = wm.Options(**OPTIONS)
    sales, freight, rent = OPTIONS["fulfilled_order_accounts"][0], OPTIONS["freight_accounts"][0], OPTIONS["rent_accounts"][0]
    day = date(2026, 7, 9)
    table = dict(wm.metrics([
        txn(sales, day, -1000), txn(sales, day, -0.01), txn(sales, day, 50, "Receivable Credit Note"),
        txn(freight, day, 300, description="ppnd enterprises - delivery"), txn(freight, day, 20, description="Courier - x"),
        txn(rent, day, 5000, description="Michael Oldmeadow - Rent"), txn(rent, day, 900, description="Office Lessor - Rent"),
    ], [END, date(2026, 8, 31)], options, {END: Decimal(7)}))
    assert table["Fulfilled orders - $"][END] == 1000 and table["Fulfilled orders - Qty (invoice lines)"][END] == 1
    assert table["Outbound freight - PPND - $"][END] == 300 and table["Outbound freight - Other - $"][END] == 20
    assert table["Warehouse rent - $"][END] == 5000 and table["Average revenue per sales order - $"][END] == 7
    assert table["Fulfilled orders $ less COGS - %"][END] == 100
    assert table["Outbound freight / COGS - %"][END] is None and table["Average variable cost per fulfilled order - $"][date(2026, 8, 31)] is None


def test_options_reject_account_in_two_groups():
    with pytest.raises(ValueError, match="more than one group"):
        wm.Options(**{**OPTIONS, "freight_accounts": OPTIONS["cogs_accounts"][:1]})


def test_validate_reports_bad_options(write_config):
    settings = config.load(write_config('[[reports]]\nid = "warehouse_metrics"\n[reports.options]\nmonths = 0\n'))
    errors = validate(settings, mock=True)
    assert any(e.startswith("report 'warehouse_metrics': invalid options: months:") for e in errors)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\n[reports.options]\nx = 1\n'))
    assert "report 'sales_summary': takes no options" in validate(settings, mock=True)


def test_needs_org_short_code_outside_mock():
    settings = Settings(reports=[ReportConfig(id="warehouse_metrics", options=OPTIONS)])
    assert "missing xero.org_short_code (config.toml)" in validate(settings)


@pytest.mark.parametrize("months, first", [(2, "2026-09"), (None, "2026-07")])
def test_build_limits_months_only_when_set(months, first):
    definition = ReportConfig(id="warehouse_metrics", options={**OPTIONS, "months": months})
    data = wm.build(DataSources(Settings(), ROOT / "mock"), definition, date(2026, 11, 18))
    assert data["metric_columns"][1] == first and data["metric_columns"][-1] == "2026-10"
    assert data["period_end"] == date(2026, 10, 31)
    assert all(row["date"] >= data["period_start"] for row in data["transaction_rows"])


def test_mock_run_writes_metrics_workbook(write_config, tmp_path):
    text = (ROOT / "config.toml").read_text().replace('setup_dir = "claude"', f'setup_dir = "{ROOT / "claude"}"')
    settings = config.load(write_config(text))
    settings = settings.model_copy(update={"reports": [r for r in settings.reports if r.id == "warehouse_metrics"]})
    assert run_all(settings, tmp_path, mock_dir=ROOT / "mock", ai=False) == 0
    wb = load_workbook(tmp_path / "warehouse_metrics-2026-11-18.xlsx")
    assert wb.sheetnames == ["Summary", "Metrics", "Transactions"]
    assert [c.value for c in wb["Metrics"][5]] == ["Metric", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert wb["Summary"]["B6"].value == "2026-07-01 to 2026-10-31" and wb["Summary"]["B7"].value.startswith("AUD ")
