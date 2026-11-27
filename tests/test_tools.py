import asyncio
import json
from pathlib import Path

from report_clerk import config
from report_clerk.analysis import tools
from report_clerk.services import DataSources

ALL_TOOLS = ["shopify_sales_comparison", "shopify_daily_sales", "shopify_sales_by_category", "shopify_top_products"]

ROOT = Path(__file__).parents[1]


def test_only_configured_sources_get_tools(write_config, monkeypatch):
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "x")
    settings = config.load(write_config('[shopify]\nshop = "company-dev"\n'))
    assert sorted(t.name for t in tools.build(DataSources(settings), ALL_TOOLS)) == ["shopify_daily_sales", "shopify_sales_by_category", "shopify_sales_comparison", "shopify_top_products"]


def test_tool_returns_json_from_mock_data(write_config):
    services = DataSources(config.load(write_config("")), mock_dir=ROOT / "mock")
    by_name = {t.name: t for t in tools.build(services, ALL_TOOLS)}
    result = asyncio.run(by_name["shopify_sales_by_category"].handler({"days": 7}))
    assert {row["category"] for row in json.loads(result["content"][0]["text"])} <= {"Ice Bath", "Sauna", "E-Bike"}


def test_tool_error_is_returned_not_raised(write_config, monkeypatch):
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "x")
    services = DataSources(config.load(write_config('[shopify]\nshop = "company-dev"\n')))
    monkeypatch.setattr(services.shopify, "_graphql", lambda q, v: (_ for _ in ()).throw(RuntimeError("down")))
    result = asyncio.run(tools.build(services, ALL_TOOLS)[0].handler({}))
    assert result["is_error"] and "RuntimeError: down" in result["content"][0]["text"]


def test_every_tool_answers_from_mock_data(write_config):
    services = DataSources(config.load(write_config("")), mock_dir=ROOT / "mock")
    args = {"shopify_top_products": {"limit": 3}}
    for t in tools.build(services, ALL_TOOLS):
        result = asyncio.run(t.handler(args.get(t.name, {})))
        assert not result.get("is_error"), (t.name, result)
        assert json.loads(result["content"][0]["text"]), t.name


def test_report_allowlist_blocks_other_configured_sources(write_config):
    services = DataSources(config.load(write_config('')), mock_dir=ROOT / 'mock')
    assert tools.build(services, []) == []
    assert [t.name for t in tools.build(services, ['shopify_daily_sales'])] == ['shopify_daily_sales']


def test_unknown_tools_rejected(write_config):
    import pytest
    services = DataSources(config.load(write_config('')), mock_dir=ROOT / 'mock')
    with pytest.raises(ValueError, match='Unknown Claude tools'):
        tools.build(services, ['typo'])
