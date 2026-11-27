from pathlib import Path

from report_clerk import config
from report_clerk.run import run_all
from report_clerk.validate import validate

from conftest import WAREHOUSE, summary_sheet

ROOT = Path(__file__).parents[1]
ALL_REPORTS = '[[reports]]\nid = "sales_summary"\n' + WAREHOUSE


def test_mock_run_writes_every_report(write_config, tmp_path):
    settings = config.load(write_config(ALL_REPORTS))
    assert validate(settings, mock=True) == []
    assert run_all(settings, tmp_path / "out", mock_dir=ROOT / "mock") == 0

    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["sales_summary-2026-11-18.xlsx", "warehouse_metrics-2026-11-18.xlsx"]
    sales = dict(summary_sheet(tmp_path / "out" / "sales_summary-2026-11-18.xlsx"))
    assert sales["Period"] == "2026-10-20 to 2026-11-18" and sales["Net merchandise sales"].startswith("AUD ")
    assert dict(summary_sheet(tmp_path / "out" / "warehouse_metrics-2026-11-18.xlsx"))["Period"] == "2026-07-01 to 2026-10-31"


def test_mock_run_writes_spreadsheets(write_config, tmp_path):
    from openpyxl import load_workbook

    settings = config.load(write_config(ALL_REPORTS))
    run_all(settings, tmp_path, mock_dir=ROOT / "mock")

    wb = load_workbook(tmp_path / "sales_summary-2026-11-18.xlsx")
    assert wb.sheetnames == ["Summary", "By category", "Top products", "Daily"]
    ws = wb["By category"]
    assert [c.value for c in ws[5]] == ["Category", "Order count", "Units", "Net merchandise sales"]
    assert {ws.cell(r, 1).value for r in range(6, ws.max_row + 1)} == {"Ice Bath", "Sauna", "E-Bike"}
    assert ws.cell(6, 4).number_format == "#,##0.00" and len(ws._images) == 1


def test_claude_failure_still_delivers_report(write_config, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("login expired")

    monkeypatch.setattr("report_clerk.analysis.agent.analyse", fail)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\n'))
    assert run_all(settings, tmp_path, mock_dir=ROOT / "mock") == 1
    assert any("Analysis unavailable" in str(v) for _, v in summary_sheet(tmp_path / "sales_summary-2026-11-18.xlsx"))


def test_failing_report_does_not_stop_others(write_config, tmp_path, monkeypatch):
    monkeypatch.setattr("report_clerk.reports.sales_summary.build", lambda *a: 1 / 0)
    settings = config.load(write_config(ALL_REPORTS))
    assert run_all(settings, tmp_path, mock_dir=ROOT / "mock") == 1
    failed = summary_sheet(tmp_path / "sales_summary-2026-11-18.xlsx")
    assert failed[0][0] == "Period" and failed[1][0] == "Issue" and "Report failed: division by zero" in failed[1][1]
    from openpyxl import load_workbook
    assert load_workbook(tmp_path / "sales_summary-2026-11-18.xlsx").sheetnames == ["Summary"]
    assert dict(summary_sheet(tmp_path / "warehouse_metrics-2026-11-18.xlsx"))["Period"] == "2026-07-01 to 2026-10-31"


def test_report_passes_only_explicit_claude_tools(write_config, tmp_path, monkeypatch):
    granted = []
    monkeypatch.setattr("report_clerk.run.analysis_errors", lambda settings: [])
    def analyse(config, services, prompt, title, data, allowed_tools, **kwargs):
        granted.append(allowed_tools)
        return 'Done'
    monkeypatch.setattr('report_clerk.analysis.agent.analyse', analyse)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\nclaude_tools = ["shopify_daily_sales"]\n'))
    assert run_all(settings, tmp_path, mock_dir=ROOT / 'mock') == 0
    assert granted == [['shopify_daily_sales']]
