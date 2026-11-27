from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import logging
from statistics import median
import time as clock
from zoneinfo import ZoneInfo

import requests
from pydantic import BaseModel

from report_clerk.config import ShopifyConfig

# Use the same net merchandise amount in every sales breakdown.
LINE_FIELDS = """
  title
  currentQuantity
  priceAfterAllDiscountsBeforeTaxesSet { shopMoney { amount currencyCode } }
  product { productType }
"""
ORDERS_QUERY = """
query Orders($cursor: String, $query: String) {
  orders(first: 10, after: $cursor, query: $query, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      cancelledAt
      lineItems(first: 10) {
        pageInfo { hasNextPage endCursor }
        nodes { %s }
      }
    }
  }
}
""" % LINE_FIELDS
LINE_ITEMS_QUERY = """
query OrderLines($id: ID!, $cursor: String) {
  order(id: $id) {
    lineItems(first: 50, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes { %s }
    }
  }
}
""" % LINE_FIELDS


class LineItem(BaseModel):
    title: str
    category: str
    quantity: int
    net_merchandise_sales: Decimal


class Order(BaseModel):
    name: str
    created: date
    net_merchandise_sales: Decimal
    currency: str
    lines: list[LineItem]


class CategorySales(BaseModel):
    category: str
    order_count: int
    units: int
    net_merchandise_sales: Decimal


class ProductSales(BaseModel):
    title: str
    category: str
    units: int
    net_merchandise_sales: Decimal


class DailySales(BaseModel):
    day: date
    order_count: int
    net_merchandise_sales: Decimal


class ShopifyService:
    timezone_name = "Australia/Brisbane"

    def __init__(self, config: ShopifyConfig, timezone_name: str = "Australia/Brisbane"):
        self.timezone_name = timezone_name
        self.config = config
        self.url = f"https://{config.shop}.myshopify.com/admin/api/{config.api_version}/graphql.json"

    def _graphql(self, query: str, variables: dict) -> dict:
        log = logging.getLogger(__name__)
        for attempt in range(4):
            response = requests.post(
                self.url, json={"query": query, "variables": variables},
                headers={"X-Shopify-Access-Token": self.config.access_token}, timeout=30,
            )
            if response.status_code in (429, 502, 503, 504) and attempt < 3:
                log.warning('Shopify retry attempt=%d status=%d', attempt + 1, response.status_code)
                clock.sleep(min(2 ** attempt, 4))
                continue
            response.raise_for_status()
            body = response.json()
            cost = body.get('extensions', {}).get('cost', {})
            log.debug('Shopify requested_cost=%s actual_cost=%s', cost.get('requestedQueryCost'), cost.get('actualQueryCost'))
            errors = body.get('errors', [])
            if errors and all(e.get('extensions', {}).get('code') == 'THROTTLED' for e in errors) and attempt < 3:
                log.warning('Shopify throttled attempt=%d', attempt + 1)
                clock.sleep(min(2 ** attempt, 4))
                continue
            if errors:
                codes = [e.get('extensions', {}).get('code', 'UNKNOWN') for e in errors]
                raise RuntimeError(f'Shopify GraphQL request failed: {codes}')
            return body['data']

    def orders(self, since: date, until: date | None = None) -> list[Order]:
        # Orders older than 60 days need the read_all_orders scope.
        zone = ZoneInfo(self.timezone_name)
        until = until or datetime.now(zone).date() - timedelta(days=1)
        start = datetime.combine(since, time.min, zone).astimezone(timezone.utc).isoformat()
        end = datetime.combine(until + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat()
        query = f"created_at:>={start} created_at:<{end}"
        orders, cursor, seen_orders = [], None, set()
        for _ in range(500):
            data = self._graphql(ORDERS_QUERY, {"cursor": cursor, "query": query})
            page = data["orders"]
            for node in page["nodes"]:
                if node["cancelledAt"]:
                    continue
                lines = list(node["lineItems"]["nodes"])
                line_page = node["lineItems"]
                seen = set()
                while line_page["pageInfo"]["hasNextPage"]:
                    line_cursor = line_page["pageInfo"]["endCursor"]
                    if not line_cursor or line_cursor in seen or len(seen) >= 500:
                        raise RuntimeError("Shopify line item pagination did not advance")
                    seen.add(line_cursor)
                    line_page = self._graphql(LINE_ITEMS_QUERY, {"id": node["id"], "cursor": line_cursor})["order"]["lineItems"]
                    lines.extend(line_page["nodes"])
                order = _order({**node, "lineItems": {"nodes": lines}}, self.timezone_name)
                # Count orders retaining merchandise, consistently with category counts and AOV.
                if order.lines and since <= order.created <= until:
                    orders.append(order)
            if not page["pageInfo"]["hasNextPage"]:
                return orders
            cursor = page["pageInfo"]["endCursor"]
            if not cursor or cursor in seen_orders:
                raise RuntimeError('Shopify order pagination did not advance')
            seen_orders.add(cursor)
        raise RuntimeError('Shopify order page limit exceeded')

    def sales_by_category(self, since: date) -> list[CategorySales]:
        return sales_by_category(self.orders(since))

    def top_products(self, since: date, limit: int = 10) -> list[ProductSales]:
        return top_products(self.orders(since), limit)

    def daily_sales(self, since: date) -> list[DailySales]:
        return daily_sales(self.orders(since))


def _order(node: dict, timezone_name: str = "Australia/Brisbane") -> Order:
    nodes = node["lineItems"]["nodes"]
    currencies = {li["priceAfterAllDiscountsBeforeTaxesSet"]["shopMoney"]["currencyCode"] for li in nodes}
    if len(currencies) != 1:
        raise ValueError("Expected one shop currency per order")
    lines = [
        LineItem(
            title=li["title"],
            category=(li["product"] or {}).get("productType") or "Other",
            quantity=li["currentQuantity"],
            net_merchandise_sales=Decimal(li["priceAfterAllDiscountsBeforeTaxesSet"]["shopMoney"]["amount"]),
        )
        for li in nodes if li["currentQuantity"] > 0
    ]
    return Order(
        name=node["name"],
        created=datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")).astimezone(ZoneInfo(timezone_name)).date(),
        net_merchandise_sales=sum((line.net_merchandise_sales for line in lines), Decimal(0)),
        currency=currencies.pop(),
        lines=lines,
    )


def sales_by_category(orders: list[Order]) -> list[CategorySales]:
    net_merchandise_sales: dict[str, Decimal] = defaultdict(Decimal)
    units: dict[str, int] = defaultdict(int)
    order_names: dict[str, set[str]] = defaultdict(set)
    for order in orders:
        for line in order.lines:
            net_merchandise_sales[line.category] += line.net_merchandise_sales
            units[line.category] += line.quantity
            order_names[line.category].add(order.name)
    rows = [CategorySales(category=c, order_count=len(order_names[c]), units=units[c], net_merchandise_sales=net_merchandise_sales[c]) for c in net_merchandise_sales]
    return sorted(rows, key=lambda r: r.net_merchandise_sales, reverse=True)


def top_products(orders: list[Order], limit: int = 10) -> list[ProductSales]:
    totals: dict[tuple[str, str], ProductSales] = {}
    for order in orders:
        for line in order.lines:
            key = (line.title, line.category)
            row = totals.setdefault(key, ProductSales(title=line.title, category=line.category, units=0, net_merchandise_sales=Decimal(0)))
            row.units += line.quantity
            row.net_merchandise_sales += line.net_merchandise_sales
    return sorted(totals.values(), key=lambda r: r.net_merchandise_sales, reverse=True)[:limit]


def daily_sales(orders: list[Order]) -> list[DailySales]:
    days: dict[date, DailySales] = {}
    for order in orders:
        row = days.setdefault(order.created, DailySales(day=order.created, order_count=0, net_merchandise_sales=Decimal(0)))
        row.order_count += 1
        row.net_merchandise_sales += order.net_merchandise_sales
    return sorted(days.values(), key=lambda r: r.day)


# Sales comparisons calculated before the report is passed to an analyst.

def _change(current: Decimal, previous: Decimal) -> dict:
    return {
        "amount": current - previous,
        "percent": ((current - previous) / previous * 100).quantize(Decimal('0.1')) if previous else None,
        "new": previous == 0 and current != 0,
    }


def _period(orders: list[Order], start: date, end: date) -> dict:
    orders = [o for o in orders if start <= o.created <= end]
    daily = {start + timedelta(days=i): Decimal(0) for i in range((end - start).days + 1)}
    for order in orders:
        daily[order.created] += order.net_merchandise_sales
    median_daily_sales = median(daily.values())
    high_sales_days = [{"day": day, "net_merchandise_sales": value} for day, value in daily.items() if value > 3 * median_daily_sales]
    net_merchandise_sales = sum(daily.values(), Decimal(0))
    return {
        "period_start": start, "period_end": end, "days": len(daily), "order_count": len(orders),
        "net_merchandise_sales": net_merchandise_sales, "median_daily_net_merchandise_sales": median_daily_sales,
        "high_sales_days": high_sales_days,
        "net_merchandise_sales_without_high_sales_days": net_merchandise_sales - sum((o['net_merchandise_sales'] for o in high_sales_days), Decimal(0)),
        "categories": sales_by_category(orders),
    }


def compare_sales(orders: list[Order], period_end: date, days: int) -> dict:
    if not 1 <= days <= 3650:
        raise ValueError('days must be between 1 and 3650')
    currencies = {o.currency for o in orders}
    if len(currencies) > 1:
        raise ValueError('Cannot compare different currencies')
    period_start = period_end - timedelta(days=days - 1)
    current = _period(orders, period_start, period_end)
    previous = _period(orders, period_start - timedelta(days=days), period_start - timedelta(days=1))
    current_categories = {r.category: r.net_merchandise_sales for r in current['categories']}
    previous_categories = {r.category: r.net_merchandise_sales for r in previous['categories']}
    categories = [
        {"category": name, "current": current_categories.get(name, Decimal(0)),
         "previous": previous_categories.get(name, Decimal(0)),
         "change": _change(current_categories.get(name, Decimal(0)), previous_categories.get(name, Decimal(0)))}
        for name in sorted(current_categories.keys() | previous_categories.keys())
    ]
    return {
        "currency": next(iter(currencies), None), "current": current, "previous": previous,
        "change": _change(current['net_merchandise_sales'], previous['net_merchandise_sales']),
        "change_without_high_sales_days": _change(current['net_merchandise_sales_without_high_sales_days'], previous['net_merchandise_sales_without_high_sales_days']),
        "categories": sorted(categories, key=lambda r: abs(r['change']['amount']), reverse=True),
        "meets_minimum_order_count": min(current['order_count'], previous['order_count']) >= 10,
    }
