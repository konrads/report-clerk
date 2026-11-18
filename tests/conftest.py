import os

import pytest

CREDENTIAL_PREFIXES = ("SHOPIFY_", "XERO_", "FISHBOWL_", "MAILGUN_", "CLAUDE_")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(CREDENTIAL_PREFIXES):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def write_config(tmp_path):
    def write(text: str):
        path = tmp_path / "config.toml"
        path.write_text(text)
        return path

    return write
