from pathlib import Path

from report_clerk import config, reports
from report_clerk.validate import validate, analysis_errors

from conftest import WAREHOUSE

BASE = """
# comment
[shopify]
shop = "company-dev"

[[reports]]
id = "sales_summary"
prompt = "trends"

# [[reports]]
# id = "warehouse_metrics"
"""


def test_toml_with_comments_and_env_merge(write_config, monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "from-env")
    (tmp_path / ".env").write_text("CLAUDE_API_KEY=from-dotenv\nSHOPIFY_ACCESS_TOKEN=from-dotenv\n")
    settings = config.load(write_config(BASE))
    assert settings.shopify.access_token == "from-env"
    assert settings.shopify.shop == "company-dev"
    assert settings.shopify.api_version == "2026-07"
    assert settings.claude.api_key == "from-dotenv"
    assert [r.id for r in settings.reports] == ["sales_summary"]


def test_unlisted_env_vars_are_ignored(write_config, monkeypatch):
    for name, value in {"SHOPIFY_SHOP": "other", "CLAUDE_EFFORT": "max", "CLAUDE_MODEL": "x", "XERO_TENANT": "x"}.items():
        monkeypatch.setenv(name, value)
    settings = config.load(write_config(BASE))
    assert (settings.shopify.shop, settings.claude.effort, settings.claude.model) == ("company-dev", "medium", "claude-opus-5-5")


def test_validate_reports_missing_credentials(write_config, monkeypatch):
    monkeypatch.setattr("report_clerk.validate.claude_logged_in", lambda: False)
    errors = validate(config.load(write_config(BASE)))
    assert "missing shopify.access_token (env SHOPIFY_ACCESS_TOKEN)" in errors
    assert not any(e.startswith("no Claude auth") for e in errors)
    assert any(e.startswith("no Claude auth") for e in analysis_errors(config.load(write_config(BASE))))
    assert not any("xero" in e for e in errors)


def test_missing_non_secret_points_at_config(write_config):
    errors = validate(config.load(write_config(WAREHOUSE)))
    assert "missing xero.org_short_code (config.toml)" in errors


def test_validate_passes_with_credentials(write_config, monkeypatch):
    for key in ("SHOPIFY_ACCESS_TOKEN", "CLAUDE_API_KEY"):
        monkeypatch.setenv(key, "x")
    setup = Path(__file__).parents[1] / "claude"
    assert validate(config.load(write_config(f'[claude]\nsetup_dir = "{setup}"\n' + BASE))) == []


def test_validate_unknown_report_and_duplicates(write_config):
    text = BASE + '\n[[reports]]\nid = "nope"\n[[reports]]\nid = "nope"\n'
    errors = validate(config.load(write_config(text)))
    assert "report 'nope': no module report_clerk.reports.nope" in errors
    assert "report 'nope' listed more than once" in errors


def test_validate_missing_claude_setup(write_config, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_API_KEY", "x")
    errors = analysis_errors(config.load(write_config(f'[claude]\nsetup_dir = "{tmp_path / "nope"}"\n' + BASE)))
    assert f"missing {tmp_path / 'nope' / 'settings.json'}" in errors


def test_validate_rejects_unsafe_report_id(write_config):
    errors = validate(config.load(write_config('[[reports]]\nid = "os.path"\n')))
    assert "report 'os.path': id must be lowercase letters, digits and _" in errors


def test_validate_no_reports(write_config):
    assert "no reports enabled in config" in validate(config.load(write_config("")))


def test_validate_incomplete_report_module(write_config, monkeypatch):
    real_load = reports.load
    monkeypatch.setattr(reports, "load", lambda i: object() if i == "sales_summary" else real_load(i))
    text = '[[reports]]\nid = "sales_summary"\n' + WAREHOUSE
    errors = validate(config.load(write_config(text)), mock=True)
    assert errors == [
        "report 'sales_summary': module must define SOURCES and build()",
    ]


def test_validate_rejects_unknown_claude_tools(write_config, monkeypatch):
    monkeypatch.setattr('report_clerk.validate.claude_logged_in', lambda: True)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\nclaude_tools = ["unknown"]\n'))
    assert "report 'sales_summary': unknown Claude tool 'unknown'" in validate(settings, mock=True)
