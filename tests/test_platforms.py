import os
from pathlib import Path

from report_clerk.analysis import agent as claude
from report_clerk.config import ClaudeConfig, Settings
from report_clerk.services import DataSources
from report_clerk.services import browser as xero_export

ROOT = Path(__file__).parents[1]


def test_notify_per_platform(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(xero_export.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(xero_export.subprocess, "Popen", lambda args, **kw: calls.append(args))
    xero_export.notify("log in", "darwin")
    xero_export.notify("log in", "win32")
    xero_export.notify("log in", "linux")
    assert [c[0] for c in calls] == ["osascript", "powershell"] and "'log in'" in calls[1][-1]
    assert capsys.readouterr().err.count("log in") == 3


def test_notify_survives_missing_program(monkeypatch, capsys):
    monkeypatch.setattr(xero_export.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("osascript")))
    xero_export.notify("log in", "darwin")
    assert "log in" in capsys.readouterr().err


def test_child_env_keeps_only_allowed_names():
    env = claude.child_env({"Path": "p", "SystemRoot": "C:\\Windows", "SHOPIFY_ACCESS_TOKEN": "s", "ANTHROPIC_API_KEY": "inherited",
                            "CLAUDE_AGENT_SDK_X": "1", "REPORT_CLERK_PASS_ANTHROPIC_API_KEY": "chosen"})
    assert env == {"Path": "p", "SystemRoot": "C:\\Windows", "CLAUDE_AGENT_SDK_X": "1", "ANTHROPIC_API_KEY": "chosen"}


def test_windows_options_trim_environ_while_open(monkeypatch):
    monkeypatch.setattr(claude, "WINDOWS", True)
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(claude=ClaudeConfig(setup_dir=ROOT / "claude", api_key="k"))
    with claude.agent_options(settings.claude, DataSources(settings, ROOT / "mock"), []) as options:
        assert options.cli_path == claude.cli_path() and options.env == {}
        assert "SHOPIFY_ACCESS_TOKEN" not in os.environ and os.environ["ANTHROPIC_API_KEY"] == "k"
    assert os.environ["SHOPIFY_ACCESS_TOKEN"] == "secret" and "ANTHROPIC_API_KEY" not in os.environ
