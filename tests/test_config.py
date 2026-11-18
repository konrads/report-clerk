from pathlib import Path

from report_clerk import config, reports
from report_clerk.validate import validate, analysis_errors

BASE = """
# comment
[shopify]
shop = "company-dev"

[mailgun]
domain = "sandbox.mailgun.org"
sender = "r@sandbox.mailgun.org"
to = ["a@example.com"]

[[reports]]
id = "sales_summary"
prompt = "trends"

# [[reports]]
# id = "stock_levels"
"""


def test_toml_with_comments_and_env_merge(write_config, monkeypatch, tmp_path):
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "from-env")
    (tmp_path / ".env").write_text("MAILGUN_API_KEY=from-dotenv\nSHOPIFY_ACCESS_TOKEN=from-dotenv\n")
    settings = config.load(write_config(BASE))
    assert settings.shopify.access_token == "from-env"
    assert settings.shopify.shop == "company-dev"
    assert settings.shopify.api_version == "2026-07"
    assert settings.mailgun.api_key == "from-dotenv"
    assert settings.mailgun.to == ["a@example.com"]
    assert [r.id for r in settings.reports] == ["sales_summary"]


def test_unlisted_env_vars_are_ignored(write_config, monkeypatch):
    for name, value in {"SHOPIFY_SHOP": "other", "CLAUDE_EFFORT": "max", "CLAUDE_MODEL": "x", "MAILGUN_TO": "x"}.items():
        monkeypatch.setenv(name, value)
    settings = config.load(write_config(BASE))
    assert (settings.shopify.shop, settings.claude.effort, settings.claude.model) == ("company-dev", "medium", "claude-opus-5-5")
    assert settings.mailgun.to == ["a@example.com"]


def test_validate_reports_missing_credentials(write_config, monkeypatch):
    monkeypatch.setattr("report_clerk.validate.claude_logged_in", lambda: False)
    errors = validate(config.load(write_config(BASE)))
    assert "missing shopify.access_token (env SHOPIFY_ACCESS_TOKEN)" in errors
    assert "missing mailgun.api_key (env MAILGUN_API_KEY)" in errors
    assert not any(e.startswith("no Claude auth") for e in errors)
    assert any(e.startswith("no Claude auth") for e in analysis_errors(config.load(write_config(BASE))))
    assert not any("xero" in e for e in errors)


def test_missing_non_secret_points_at_config(write_config):
    errors = validate(config.load(write_config('[[reports]]\nid = "profit_and_loss"\n')))
    assert "missing mailgun.domain (config.toml)" in errors


def test_validate_passes_with_credentials(write_config, monkeypatch):
    for key in ("SHOPIFY_ACCESS_TOKEN", "MAILGUN_API_KEY", "CLAUDE_API_KEY"):
        monkeypatch.setenv(key, "x")
    setup = Path(__file__).parents[1] / "claude"
    assert validate(config.load(write_config(f'[claude]\nsetup_dir = "{setup}"\n' + BASE))) == []


def test_validate_unknown_report_and_duplicates(write_config):
    text = BASE + '\n[[reports]]\nid = "nope"\n[[reports]]\nid = "nope"\n'
    errors = validate(config.load(write_config(text)), email=False)
    assert "report 'nope': no module report_clerk.reports.nope" in errors
    assert "report 'nope' listed more than once" in errors


def test_validate_missing_claude_setup(write_config, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_API_KEY", "x")
    errors = analysis_errors(config.load(write_config(f'[claude]\nsetup_dir = "{tmp_path / "nope"}"\n' + BASE)))
    assert f"missing {tmp_path / 'nope' / 'settings.json'}" in errors


def test_validate_rejects_unsafe_report_id(write_config):
    errors = validate(config.load(write_config('[[reports]]\nid = "os.path"\n')), email=False)
    assert "report 'os.path': id must be lowercase letters, digits and _" in errors


def test_validate_no_reports(write_config):
    assert "no reports enabled in config" in validate(config.load(write_config("")), email=False)


def test_validate_incomplete_report_module(write_config, monkeypatch):
    real_load = reports.load
    monkeypatch.setattr(reports, "load", lambda i: object() if i == "sales_summary" else real_load(i))
    monkeypatch.setattr(reports, "template_exists", lambda i: False)
    text = '[[reports]]\nid = "sales_summary"\n[[reports]]\nid = "stock_levels"\n'
    errors = validate(config.load(write_config(text)), email=False, mock=True)
    assert errors == [
        "report 'sales_summary': module must define SOURCES and build()",
        "report 'stock_levels': no template templates/stock_levels.html",
    ]


def test_validate_rejects_unknown_claude_tools(write_config, monkeypatch):
    monkeypatch.setattr('report_clerk.validate.claude_logged_in', lambda: True)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\nprompt = "trends"\nclaude_tools = ["unknown"]\n'))
    assert "report 'sales_summary': unknown Claude tool 'unknown'" in validate(settings, email=False, mock=True)
