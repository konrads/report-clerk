from pathlib import Path

from report_clerk import config
from report_clerk.outputs import EmailDestination, FileDestination
from report_clerk.run import run_all
from report_clerk.validate import validate

ROOT = Path(__file__).parents[1]
ALL_REPORTS = "".join(f'[[reports]]\nid = "{r}"\n' for r in ("sales_summary", "profit_and_loss", "stock_levels"))


def test_mock_run_renders_every_report(write_config, tmp_path):
    settings = config.load(write_config(ALL_REPORTS))
    assert validate(settings, email=False, mock=True) == []
    assert run_all(settings, [FileDestination(tmp_path / "out")], mock_dir=ROOT / "mock") == 0

    sales = (tmp_path / "out" / "sales_summary.html").read_text()
    assert "2026-10-20 to 2026-11-18" in sales and "E-Bike" in sales and "data:image/png;base64," in sales
    assert "Net profit" in (tmp_path / "out" / "profit_and_loss.html").read_text()
    assert "Spare Battery" in (tmp_path / "out" / "stock_levels.html").read_text()


def test_mock_run_writes_spreadsheets(write_config, tmp_path):
    from openpyxl import load_workbook

    settings = config.load(write_config(ALL_REPORTS))
    run_all(settings, [FileDestination(tmp_path)], mock_dir=ROOT / "mock")

    wb = load_workbook(tmp_path / "sales_summary-2026-11-18.xlsx")
    assert wb.sheetnames == ["By category", "Top products", "Daily"]
    ws = wb["By category"]
    assert [c.value for c in ws[5]] == ["Category", "Order count", "Units", "Net merchandise sales"]
    assert {ws.cell(r, 1).value for r in range(6, ws.max_row + 1)} == {"Ice Bath", "Sauna", "E-Bike"}
    assert ws.cell(6, 4).number_format == "#,##0.00" and len(ws._images) == 1

    pnl = load_workbook(tmp_path / "profit_and_loss-2026-11-18.xlsx")["Profit and loss"]
    assert pnl.cell(pnl.max_row, 2).value == "Net Profit"


def test_email_attaches_spreadsheet(write_config, monkeypatch):
    sent = []
    monkeypatch.setattr("report_clerk.mailgun.requests.post", lambda url, **kw: sent.append(kw) or type("R", (), {"status_code": 200, "raise_for_status": lambda self: None, "json": lambda self: {"id": "test"}})())
    text = '[mailgun]\ndomain = "d"\napi_key = "k"\nsender = "s@d"\nto = ["a@b"]\n[[reports]]\nid = "sales_summary"\n'
    settings = config.load(write_config(text))
    assert run_all(settings, [EmailDestination(settings.mailgun)], mock_dir=ROOT / "mock") == 0

    files = sent[0]["files"]
    assert [(kind, name) for kind, (name, _, _) in files] == [("inline", "company_logo.png"), ("attachment", "sales_summary-2026-11-18.xlsx")]
    assert 'src="cid:company_logo.png"' in sent[0]["data"]["html"]


def test_claude_failure_still_delivers_report(write_config, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("login expired")

    monkeypatch.setattr("report_clerk.claude.analyse", fail)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\n'))
    assert run_all(settings, [FileDestination(tmp_path)], mock_dir=ROOT / "mock") == 1
    assert "Analysis unavailable" in (tmp_path / "sales_summary.html").read_text()


def test_failing_report_does_not_stop_others(write_config, tmp_path, monkeypatch):
    monkeypatch.setattr("report_clerk.reports.sales_summary.build", lambda *a: 1 / 0)
    settings = config.load(write_config(ALL_REPORTS))
    assert run_all(settings, [FileDestination(tmp_path)], mock_dir=ROOT / "mock") == 1
    assert sorted(p.name for p in tmp_path.glob("*.html")) == ["profit_and_loss.html", "sales_summary.html", "stock_levels.html"]
    assert "Report failed" in (tmp_path / "sales_summary.html").read_text()


def test_report_passes_only_explicit_claude_tools(write_config, tmp_path, monkeypatch):
    granted = []
    monkeypatch.setattr("report_clerk.run.analysis_errors", lambda settings: [])
    def analyse(config, services, prompt, title, data, allowed_tools, **kwargs):
        granted.append(allowed_tools)
        return 'Done'
    monkeypatch.setattr('report_clerk.claude.analyse', analyse)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\nclaude_tools = ["shopify_daily_sales"]\n'))
    assert run_all(settings, [FileDestination(tmp_path)], mock_dir=ROOT / 'mock') == 0
    assert granted == [['shopify_daily_sales']]


def test_retry_partial_run_skips_delivered_email(write_config, tmp_path, monkeypatch):
    accepted = []
    fail_once = [True]
    def send(config, recipients, subject, *args, **kwargs):
        if subject == 'profit_and_loss' and fail_once[0]:
            fail_once[0] = False
            raise TimeoutError('outcome unknown')
        accepted.append(subject)
    monkeypatch.setattr('report_clerk.mailgun.send', send)
    settings = config.load(write_config(ALL_REPORTS))
    mailbox = EmailDestination(settings.mailgun, tmp_path / 'journal.sqlite3')
    assert run_all(settings, [mailbox], mock_dir=ROOT / 'mock') == 1
    assert accepted == ['sales_summary', 'stock_levels']
    assert run_all(settings, [mailbox], mock_dir=ROOT / 'mock') == 1
    assert accepted == ['sales_summary', 'stock_levels']
