import asyncio
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

import claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, create_sdk_mcp_server, query

from report_clerk.analysis import tools
from report_clerk.config import ClaudeConfig
from report_clerk.services import DataSources

log = logging.getLogger(__name__)

MCP_NAME = "company"
SETUP_FILES = ("settings.json", "cli.toml", "system-prompt.md", ".claude-plugin/plugin.json")

# The SDK merges options.env over the inherited environment, so it cannot remove variables.
# POSIX starts the CLI through this wrapper, Windows through a briefly trimmed os.environ; both keep only these names.
KEEP = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "PWD",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SDK_READS_SESSION_STATE",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "APPDATA", "LOCALAPPDATA",
    "TEMP", "TMP", "HOMEDRIVE", "HOMEPATH", "PROGRAMDATA", "PROGRAMFILES",
)
WINDOWS = os.name == "nt"
KEEP_PREFIXES = ("CLAUDE_AGENT_SDK_",)
PASS_PREFIX = "REPORT_CLERK_PASS_"

WRAPPER = """#!{python} -I
import os, sys
env = {{k: v for k, v in os.environ.items() if k in {keep!r} or k.startswith({prefixes!r})}}
env.update({{k[len({passed!r}):]: v for k, v in os.environ.items() if k.startswith({passed!r})}})
os.execve({cli!r}, [{cli!r}, *sys.argv[1:]], env)
"""


def setup_errors(setup_dir: Path) -> list[str]:
    errors = [f"missing {setup_dir / f}" for f in SETUP_FILES if not (setup_dir / f).is_file()]
    if errors:
        return errors
    try:
        json.loads((setup_dir / "settings.json").read_text())
        tomllib.loads((setup_dir / "cli.toml").read_text())
    except ValueError as e:
        errors.append(f"invalid file in {setup_dir}: {e}")
    return errors


def cli_path() -> str:
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
    found = next((p for p in (bundled / "claude.exe", bundled / "claude") if p.is_file()), None)
    return str(found) if found else shutil.which("claude") or "claude"


def child_env(environ) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if k.upper() in KEEP or k.startswith(KEEP_PREFIXES)}
    env.update({k[len(PASS_PREFIX):]: v for k, v in environ.items() if k.startswith(PASS_PREFIX)})
    return env


@contextmanager
def trimmed_environ(extra: dict[str, str]) -> Iterator[None]:
    # Windows: no exec wrapper, so os.environ is trimmed while the CLI runs and restored after.
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(child_env({**saved, **extra}))
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def logged_in() -> bool:
    try:
        out = subprocess.run([cli_path(), "auth", "status", "--json"], capture_output=True, text=True, timeout=30)
        return bool(json.loads(out.stdout).get("loggedIn"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def auth_env(config: ClaudeConfig) -> dict[str, str]:
    # Subscription first, API key second; with neither the CLI uses the local `claude` login.
    if config.oauth_token:
        return {f"{PASS_PREFIX}CLAUDE_CODE_OAUTH_TOKEN": config.oauth_token}
    if config.api_key:
        return {f"{PASS_PREFIX}ANTHROPIC_API_KEY": config.api_key}
    return {}


def analyse(config: ClaudeConfig, services: DataSources, prompt: str, report_title: str, report_data: str, allowed_tools: list[str]) -> str:
    # Bounded by the turn and cost caps in claude/cli.toml.
    return asyncio.run(_analyse(config, services, prompt, report_title, report_data, allowed_tools))


@contextmanager
def agent_options(config: ClaudeConfig, services: DataSources, allowed_tools: list[str]) -> Iterator[ClaudeAgentOptions]:
    # Everything Claude may do comes from the setup dir; only the auth, tools and paths are added here.
    setup = config.setup_dir.resolve()
    cli = tomllib.loads((setup / "cli.toml").read_text())
    environ = trimmed_environ(auth_env(config)) if WINDOWS else nullcontext()
    with tempfile.TemporaryDirectory() as bin_dir, tempfile.TemporaryDirectory() as work_dir, environ:
        if WINDOWS:
            launcher, env = cli_path(), {}
        else:
            wrapper = Path(bin_dir) / "claude"
            wrapper.write_text(WRAPPER.format(python=sys.executable, keep=KEEP, prefixes=KEEP_PREFIXES, passed=PASS_PREFIX, cli=cli_path()))
            wrapper.chmod(stat.S_IRWXU)
            launcher, env = str(wrapper), auth_env(config)
        yield ClaudeAgentOptions(
            cli_path=launcher,
            env=env,
            cwd=work_dir,
            model=config.model,
            effort=config.effort,
            system_prompt=(setup / "system-prompt.md").read_text(),
            settings=str(setup / "settings.json"),
            plugins=[{"type": "local", "path": str(setup)}],  # the setup dir is itself the plugin
            tools=cli["tools"],
            setting_sources=cli["setting_sources"],
            extra_args={flag: None for flag in cli["flags"]},
            max_turns=cli["max_turns"],
            max_budget_usd=cli["max_budget_usd"],
            mcp_servers={MCP_NAME: create_sdk_mcp_server(MCP_NAME, tools=tools.build(services, allowed_tools))},
        )


async def _analyse(config: ClaudeConfig, services: DataSources, prompt: str, report_title: str, report_data: str, allowed_tools: list[str]) -> str:
    if len(report_data.encode()) > 200_000:
        raise ValueError('Report data exceeds analysis input limit')
    ask = f"Report: {report_title}\n\nReport data (JSON):\n{report_data}\n\nRequest:\n{prompt}"
    result: ResultMessage | None = None
    with agent_options(config, services, allowed_tools) as options:
        async for message in query(prompt=ask, options=options):
            if isinstance(message, ResultMessage):
                result = message

    if result is None or result.is_error or not result.result:
        raise RuntimeError(f"Claude analysis failed: {result.subtype if result else 'no result'}")
    log.info("claude: %s turns, $%.4f", result.num_turns, result.total_cost_usd or 0)
    if len(result.result) > 20_000:
        raise ValueError('Analysis exceeds output limit')
    return result.result.strip()
