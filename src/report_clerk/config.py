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
    "XERO_CLIENT_ID": ("xero", "client_id"),
    "XERO_CLIENT_SECRET": ("xero", "client_secret"),
    "FISHBOWL_USERNAME": ("fishbowl", "username"),
    "FISHBOWL_PASSWORD": ("fishbowl", "password"),
    "MAILGUN_API_KEY": ("mailgun", "api_key"),
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


class XeroConfig(SourceConfig):
    required: ClassVar[tuple[str, ...]] = ("client_id", "client_secret")
    client_id: str = ""
    client_secret: str = ""


class FishbowlConfig(SourceConfig):
    required: ClassVar[tuple[str, ...]] = ("url", "username", "password")
    url: str = ""
    username: str = ""
    password: str = ""
    app_id: int = 7301
    app_name: str = "report-clerk"


class MailgunConfig(SourceConfig):
    required: ClassVar[tuple[str, ...]] = ("domain", "api_key", "sender", "to")
    domain: str = ""
    api_key: str = ""
    base_url: str = "https://api.mailgun.net"
    sender: str = ""
    to: list[str] = []


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
    to: list[str] = []


class ExecutionConfig(BaseModel):
    timezone: str = "Australia/Brisbane"
    report_timeout: float = Field(default=120, gt=0, le=300)
    attachment_timeout: float = Field(default=30, gt=0, le=120)
    analysis_timeout: float = Field(default=90, gt=0, le=300)
    render_timeout: float = Field(default=15, gt=0, le=60)


class Settings(BaseModel):
    execution: ExecutionConfig = ExecutionConfig()
    shopify: ShopifyConfig = ShopifyConfig()
    xero: XeroConfig = XeroConfig()
    fishbowl: FishbowlConfig = FishbowlConfig()
    mailgun: MailgunConfig = MailgunConfig()
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
