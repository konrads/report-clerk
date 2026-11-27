import asyncio
import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from claude_agent_sdk import SdkMcpTool, tool
from pydantic_core import to_jsonable_python

from report_clerk.services import DataSources

log = logging.getLogger(__name__)


def _schema(**properties: dict) -> dict:
    required = [name for name, p in properties.items() if "default" not in p]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOL_SOURCES = {
    "shopify_sales_by_category": "shopify",
    "shopify_top_products": "shopify",
    "shopify_daily_sales": "shopify",
    "shopify_sales_comparison": "shopify",
}

DAYS = {"type": "integer", "default": 30, "minimum": 1, "maximum": 365, "description": "how many days back from today"}
DATE = {"type": "string", "format": "date", "description": "YYYY-MM-DD"}


async def _call(fn: Callable[..., Any], *args: Any) -> dict[str, Any]:
    # Blocking service call off the event loop; results as JSON, failures as a tool error Claude can read.
    try:
        result = await asyncio.to_thread(fn, *args)
        text = json.dumps(to_jsonable_python(result))
        if len(text.encode()) > 512_000:
            raise ValueError('Tool result exceeds 512 KB; narrow the request')
        return {"content": [{"type": "text", "text": text}]}
    except Exception as e:
        log.warning("tool %s failed: %s", getattr(fn, "__name__", fn), e)
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "is_error": True}


def build(services: DataSources, allowed_tools: list[str]) -> list[SdkMcpTool]:
    # Tools for the sources that have credentials (all of them in mock mode), served in-process to Claude.
    unknown = set(allowed_tools) - TOOL_SOURCES.keys()
    if unknown:
        raise ValueError(f"Unknown Claude tools: {', '.join(sorted(unknown))}")
    calls = []

    def bounded_tool(name, description, schema):
        def decorate(fn):
            async def handler(args):
                try:
                    if set(args) - schema['properties'].keys():
                        raise ValueError('Unknown tool arguments')
                    for key in schema['required']:
                        if key not in args:
                            raise ValueError(f'Missing argument: {key}')
                    for key, value in args.items():
                        spec = schema['properties'][key]
                        if spec['type'] == 'integer':
                            if type(value) is not int or not spec.get('minimum', 1) <= value <= spec.get('maximum', 365):
                                raise ValueError(f'Argument {key} is outside allowed bounds')
                        elif spec.get('format') == 'date':
                            date.fromisoformat(value)
                    if 'from_date' in args:
                        start, end = date.fromisoformat(args['from_date']), date.fromisoformat(args['to_date'])
                        if not 0 <= (end - start).days < 365 or end > services.reporting_date():
                            raise ValueError('Date range must be ordered, completed, and at most 365 days')
                    identity = (name, json.dumps(args, sort_keys=True))
                    if len(calls) >= 20 or calls.count(identity) >= 3:
                        raise ValueError('Tool call budget exhausted or repeated without progress')
                    calls.append(identity)
                    return await fn(args)
                except Exception as error:
                    log.warning('tool=%s invalid_or_failed type=%s', name, type(error).__name__)
                    return {'content': [{'type': 'text', 'text': f'{type(error).__name__}: {error}'}], 'is_error': True}
            return tool(name, description, schema)(handler)
        return decorate

    configured = services.configured()
    tools: list[SdkMcpTool] = []

    def period_start(args: dict) -> date:
        return services.reporting_date() - timedelta(days=int(args.get("days", 30)) - 1)

    if "shopify" in configured:

        @bounded_tool("shopify_sales_by_category", "Shopify net merchandise sales, orders and units per product category for the last N days.", _schema(days=DAYS))
        async def shopify_sales_by_category(args: dict) -> dict:
            return await _call(services.shopify.sales_by_category, period_start(args))

        @bounded_tool(
            "shopify_top_products",
            "Best-selling Shopify products by net merchandise sales for the last N days.",
            _schema(days=DAYS, limit={"type": "integer", "default": 10, "minimum": 1, "maximum": 100}),
        )
        async def shopify_top_products(args: dict) -> dict:
            return await _call(services.shopify.top_products, period_start(args), int(args.get("limit", 10)))

        @bounded_tool("shopify_daily_sales", "Shopify orders and net merchandise sales per day for the last N days. Use for trends.", _schema(days=DAYS))
        async def shopify_daily_sales(args: dict) -> dict:
            return await _call(services.shopify.daily_sales, period_start(args))

        @bounded_tool("shopify_sales_comparison", "Equal calendar-period sales comparison, category changes, zero-sales days and high-sales days; calculations supplied by Python.", _schema(days=DAYS))
        async def shopify_sales_comparison(args: dict) -> dict:
            def comparison():
                from report_clerk.services.shopify import compare_sales
                days = int(args.get("days", 30))
                if not 1 <= days <= 3650:
                    raise ValueError("days must be between 1 and 3650")
                period_end = services.reporting_date()
                orders = services.shopify.orders(period_end - timedelta(days=2 * days - 1), period_end)
                return compare_sales(orders, period_end, days)
            return await _call(comparison)

        tools += [shopify_sales_by_category, shopify_top_products, shopify_daily_sales, shopify_sales_comparison]

    return [t for t in tools if t.name in allowed_tools]
