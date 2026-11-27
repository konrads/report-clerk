from pathlib import Path
from typing import ClassVar

import os
import tomllib

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG = Path("config.toml")

# The only values read from the environment (or .env); everything else comes from config.toml.
CREDENTIALS = {
    "SHOPIFY_ACCESS_TOKEN": ("shopify", "access_token"),
    "CLAUDE_OAUTH_TOKEN": ("claude", "oauth_token"),
    "CLAUDE_API_KEY": ("claude", "api_key"),
}
ENV_NAMES = {field: name for name, field in CREDENTIALS.items()}


class SourceConfig(BaseModel):
    required: ClassVar[tuple[str, ...]] = ()

    def missing(self) -> list[str]:
        return [f for f in self.required if not getattr(self, f)]


class ShopifyConfig(SourceConfig):
    required: ClassVar[tuple[str, ...]] = ("shop", "access_token")
    shop: str = ""
    access_token: str = ""
    api_version: str = "2026-07"


class BrowserConfig(SourceConfig):
    # Shared by services that drive a website; the profile holds the login session.
    profile_dir: Path = Path("~/.report-clerk/browser-profile")
    headed: bool = False
    screenshot_dir: Path = Path("out")
    download_dir: Path | None = None  # keep downloaded files here; default: a temp dir


class XeroConfig(BrowserConfig):
    # Xero web-app report exports, the only Xero source.
    required: ClassVar[tuple[str, ...]] = ("org_short_code",)
    org_short_code: str = ""
    profile_dir: Path = Path("~/.report-clerk/xero-profile")


class ClaudeConfig(SourceConfig):
    oauth_token: str = ""
    api_key: str = ""
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    setup_dir: Path = Path("claude")


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    days: int = Field(default=30, ge=1, le=3650)
    prompt: str = ""
    claude_tools: list[str] = []
    options: dict = {}  # report-specific settings, validated by the report module


class Settings(BaseModel):
    timezone: str = "Australia/Brisbane"  # reporting date is yesterday here
    shopify: ShopifyConfig = ShopifyConfig()
    xero: XeroConfig = XeroConfig()
    claude: ClaudeConfig = ClaudeConfig()
    reports: list[ReportConfig] = []


def load(path: Path = DEFAULT_CONFIG, env_file: Path = Path(".env")) -> Settings:
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    data = tomllib.loads(path.read_text())
    env = {**dotenv_values(env_file), **os.environ}
    for name, (section, field) in CREDENTIALS.items():
        if env.get(name):
            data.setdefault(section, {})[field] = env[name]
    return Settings.model_validate(data)
