import sys
from pathlib import Path

import pytest

from report_clerk import cli

ROOT = Path(__file__).parents[1]
REPORTS = '[[reports]]\nid = "sales_summary"\n[[reports]]\nid = "stock_levels"\n'


def run_cli(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["report-clerk", *args])
    try:
        cli.main()
    except SystemExit as e:
        return e.code or 0
    return 0


def test_check_ok(write_config, monkeypatch, capsys):
    path = write_config('[shopify]\nshop = "s"\n[mailgun]\ndomain = "d"\nsender = "s@d"\nto = ["a@b"]\n' + REPORTS)
    for name in ("SHOPIFY_ACCESS_TOKEN", "MAILGUN_API_KEY", "FISHBOWL_USERNAME", "FISHBOWL_PASSWORD"):
        monkeypatch.setenv(name, "x")
    path.write_text(path.read_text().replace('[mailgun]', '[fishbowl]\nurl = "https://f"\n[mailgun]'))
    assert run_cli(monkeypatch, "--config", str(path), "check") == 0
    assert "ok: 2 report(s): sales_summary, stock_levels" in capsys.readouterr().out


def test_check_reports_errors(write_config, monkeypatch, capsys):
    assert run_cli(monkeypatch, "--config", str(write_config(REPORTS)), "check") == 1
    err = capsys.readouterr().err
    assert "config error: missing shopify.access_token (env SHOPIFY_ACCESS_TOKEN)" in err
    assert "config error: missing mailgun.domain (config.toml)" in err


def test_run_mock_writes_files(write_config, monkeypatch, tmp_path):
    out = tmp_path / "out"
    args = ("--config", str(write_config(REPORTS)), "run", "--mock", str(ROOT / "mock"), "--no-email", "--no-ai", "--out", str(out))
    assert run_cli(monkeypatch, *args) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "sales_summary-2026-11-18.xlsx", "sales_summary.html", "stock_levels-2026-11-18.xlsx", "stock_levels.html",
    ]


def test_run_mock_needs_dataset(write_config, monkeypatch, tmp_path, caplog):
    args = ("--config", str(write_config(REPORTS)), "run", "--mock", str(tmp_path), "--no-email", "--no-ai", "--out", str(tmp_path / "out"))
    assert run_cli(monkeypatch, *args) == 1
    assert "reporting date unavailable" in caplog.text


def test_missing_config_file(tmp_path, monkeypatch):
    assert run_cli(monkeypatch, "--config", str(tmp_path / "nope.toml"), "check") == 1


def test_optional_analysis_setup_does_not_block_run(write_config, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr('report_clerk.validate.claude_logged_in', lambda: False)
    path = write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\n')
    assert run_cli(monkeypatch, '--config', str(path), 'run', '--mock', str(ROOT / 'mock'), '--no-email', '--out', str(tmp_path / 'out')) == 1
    assert 'failed duration_seconds' in caplog.text
    assert (tmp_path / 'out' / 'sales_summary.html').exists()


def test_check_still_reports_analysis_setup_errors(write_config, monkeypatch, capsys):
    monkeypatch.setattr('report_clerk.validate.claude_logged_in', lambda: False)
    path = write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\n')
    assert run_cli(monkeypatch, '--config', str(path), 'check') == 1
    assert 'no Claude auth' in capsys.readouterr().err
