import sys
from pathlib import Path


from report_clerk import cli

from conftest import WAREHOUSE

ROOT = Path(__file__).parents[1]
REPORTS = '[[reports]]\nid = "sales_summary"\n' + WAREHOUSE


def run_cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["report-clerk", *args])
    try:
        cli.main()
    except SystemExit as e:
        return e.code or 0
    return 0


def test_check_ok(write_config, monkeypatch, capsys):
    path = write_config('[shopify]\nshop = "s"\n[xero]\norg_short_code = "x"\n' + REPORTS)
    for name in ("SHOPIFY_ACCESS_TOKEN",):
        monkeypatch.setenv(name, "x")
    assert run_cli(monkeypatch, "--config", str(path), "check") == 0
    assert "ok: 2 report(s): sales_summary, warehouse_metrics" in capsys.readouterr().out


def test_check_reports_errors(write_config, monkeypatch, capsys):
    assert run_cli(monkeypatch, "--config", str(write_config(REPORTS)), "check") == 1
    err = capsys.readouterr().err
    assert "config error: missing shopify.access_token (env SHOPIFY_ACCESS_TOKEN)" in err
    assert "config error: missing xero.org_short_code (config.toml)" in err


def test_run_mock_writes_files(write_config, monkeypatch, tmp_path):
    out = tmp_path / "out"
    args = ("--config", str(write_config(REPORTS)), "run", "--mock", str(ROOT / "mock"), "--no-ai", "--out", str(out))
    assert run_cli(monkeypatch, *args) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "sales_summary-2026-11-18.xlsx", "warehouse_metrics-2026-11-18.xlsx",
    ]


def test_run_mock_needs_dataset(write_config, monkeypatch, tmp_path, caplog):
    args = ("--config", str(write_config(REPORTS)), "run", "--mock", str(tmp_path), "--no-ai", "--out", str(tmp_path / "out"))
    assert run_cli(monkeypatch, *args) == 1
    assert "reporting date unavailable" in caplog.text


def test_missing_config_file(tmp_path, monkeypatch):
    assert run_cli(monkeypatch, "--config", str(tmp_path / "nope.toml"), "check") == 1


def test_optional_analysis_setup_does_not_block_run(write_config, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr('report_clerk.validate.claude_logged_in', lambda: False)
    path = write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\n')
    assert run_cli(monkeypatch, '--config', str(path), 'run', '--mock', str(ROOT / 'mock'), '--out', str(tmp_path / 'out')) == 1
    assert 'analysis failed report=sales_summary' in caplog.text
    assert (tmp_path / 'out' / 'sales_summary-2026-11-18.xlsx').exists()


def test_check_still_reports_analysis_setup_errors(write_config, monkeypatch, capsys):
    monkeypatch.setattr('report_clerk.validate.claude_logged_in', lambda: False)
    path = write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\n')
    assert run_cli(monkeypatch, '--config', str(path), 'check') == 1
    assert 'no Claude auth' in capsys.readouterr().err


def repo_config(write_config):
    return str(write_config((ROOT / "config.toml").read_text()))


def test_run_only_selected_report(write_config, monkeypatch, tmp_path):
    out = tmp_path / "out"
    args = ("--config", repo_config(write_config), "run", "--mock", str(ROOT / "mock"), "--no-ai", "--only", "warehouse_metrics", "--out", str(out))
    assert run_cli(monkeypatch, *args) == 0
    assert sorted(p.name for p in out.iterdir()) == ["warehouse_metrics-2026-11-18.xlsx"]
    assert run_cli(monkeypatch, "--config", repo_config(write_config), "run", "--mock", "--only", "nope") == 1


def test_xero_transactions_and_summary_in_mock(write_config, monkeypatch, capsys):
    base = ("--config", repo_config(write_config), "xero")
    assert run_cli(monkeypatch, *base, "transactions", "--from", "2026-08-01", "--to", "2026-08-31", "--mock", str(ROOT / "mock")) == 0
    out = capsys.readouterr().out
    assert "320 - Freight Cost: 10 rows, net 15,835.93" in out and "InvAdj - Inventory Adjustment: 0 rows" in out
    assert run_cli(monkeypatch, *base, "summary", "--month", "2026-09", "--mock", str(ROOT / "mock")) == 0
    assert "Average value of invoices: 3595" in capsys.readouterr().out


def test_xero_transactions_needs_accounts(write_config, monkeypatch, capsys):
    path = str(write_config('[[reports]]\nid = "sales_summary"\n'))
    assert run_cli(monkeypatch, "--config", path, "xero", "transactions", "--from", "2026-08-01", "--to", "2026-08-31", "--mock", str(ROOT / "mock")) == 1
    assert "no accounts" in capsys.readouterr().err
    assert run_cli(monkeypatch, "--config", path, "xero", "login") == 1
    assert "missing xero.org_short_code" in capsys.readouterr().err


def test_xero_login_uses_session_with_cli_overrides(write_config, monkeypatch, capsys, tmp_path):
    from contextlib import contextmanager

    seen = []

    @contextmanager
    def session(config):
        seen.append(config)
        yield type("Session", (), {"base": "https://reporting.xero.com/!SWP1y"})()

    monkeypatch.setattr("report_clerk.services.xero.session", session)
    assert run_cli(monkeypatch, "--config", repo_config(write_config), "xero", "login", "--headed") == 0
    assert seen[0].headed and "logged in: https://reporting.xero.com/!SWP1y" in capsys.readouterr().out
