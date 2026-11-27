import os
from pathlib import Path

import pytest

CREDENTIAL_PREFIXES = ("SHOPIFY_", "CLAUDE_")
_CONFIG = (Path(__file__).parents[1] / "config.toml").read_text()
WAREHOUSE = _CONFIG[_CONFIG.index('[[reports]]\nid = "warehouse_metrics"'):]  # the repo's warehouse_metrics block


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(CREDENTIAL_PREFIXES):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def summary_sheet(path) -> list[tuple]:
    from openpyxl import load_workbook

    ws = load_workbook(path)["Summary"]
    return [tuple(c.value for c in row) for row in ws.iter_rows(min_row=6, max_col=2)]


@pytest.fixture
def write_config(tmp_path):
    def write(text: str):
        path = tmp_path / "config.toml"
        path.write_text(text)
        return path

    return write
