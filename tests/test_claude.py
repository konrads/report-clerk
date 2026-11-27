import os
import subprocess
import sys

import pytest

from report_clerk.analysis import agent as claude
from report_clerk.config import ClaudeConfig


def test_auth_prefers_subscription():
    assert claude.auth_env(ClaudeConfig(oauth_token="t", api_key="k")) == {"REPORT_CLERK_PASS_CLAUDE_CODE_OAUTH_TOKEN": "t"}
    assert claude.auth_env(ClaudeConfig(api_key="k")) == {"REPORT_CLERK_PASS_ANTHROPIC_API_KEY": "k"}
    assert claude.auth_env(ClaudeConfig()) == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX exec wrapper")
def test_wrapper_strips_credentials(tmp_path):
    wrapper = tmp_path / "claude"
    wrapper.write_text(
        claude.WRAPPER.format(python=sys.executable, keep=claude.KEEP, prefixes=claude.KEEP_PREFIXES, passed=claude.PASS_PREFIX, cli="/usr/bin/env")
    )
    wrapper.chmod(0o700)
    env = {
        **os.environ,
        "SHOPIFY_ACCESS_TOKEN": "secret",
        "CLAUDE_API_KEY": "secret",
        "ANTHROPIC_API_KEY": "inherited",
        "CLAUDE_CODE_OAUTH_TOKEN": "inherited",
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
        "CLAUDE_CODE_MESSAGING_TOKEN": "parent-session",
        "REPORT_CLERK_PASS_CLAUDE_CODE_OAUTH_TOKEN": "chosen",
    }
    out = subprocess.run([str(wrapper)], env=env, capture_output=True, text=True, check=True).stdout
    child = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert child["CLAUDE_CODE_OAUTH_TOKEN"] == "chosen"
    assert child["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
    assert "PATH" in child
    for name in ("SHOPIFY_ACCESS_TOKEN", "CLAUDE_API_KEY", "CLAUDE_CODE_MESSAGING_TOKEN", "ANTHROPIC_API_KEY", "REPORT_CLERK_PASS_CLAUDE_CODE_OAUTH_TOKEN"):
        assert name not in child


def test_cli_starts_with_only_the_configured_capabilities():
    # Starts the real bundled CLI and returns at its init message; with a login, a short model request may start.
    import asyncio
    from pathlib import Path

    from claude_agent_sdk import SystemMessage, query

    from report_clerk.config import Settings
    from report_clerk.services import DataSources

    root = Path(__file__).parents[1]
    settings = Settings(claude=ClaudeConfig(setup_dir=root / "claude"))
    services = DataSources(settings, mock_dir=root / "mock")

    async def init() -> dict:
        with claude.agent_options(settings.claude, services, ["shopify_daily_sales"]) as options:
            async for message in query(prompt="hi", options=options):
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    return message.data
        raise AssertionError("no init message")

    data = asyncio.run(init())
    tools = ["mcp__company__shopify_daily_sales"]
    assert sorted(data["tools"]) == sorted(["Skill", *tools])
    assert data["skills"] == ["company-report:sales-trends"]
    assert data["permissionMode"] == "dontAsk"
    assert [s["name"] for s in data["mcp_servers"]] == ["company"]
