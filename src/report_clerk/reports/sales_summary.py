from datetime import date, timedelta

from report_clerk.config import ReportConfig
from report_clerk.services import DataSources
from report_clerk.services import shopify

SOURCES = ("shopify",)
SHEETS = [
    ("By category", "categories", ["category", "order_count", "units", "net_merchandise_sales"]),
    ("Top products", "top_products", ["title", "category", "units", "net_merchandise_sales"]),
    ("Daily", "daily", ["day", "order_count", "net_merchandise_sales"]),
]


def build(services: DataSources, definition: ReportConfig, reporting_date: date) -> dict:
    period_start = reporting_date - timedelta(days=definition.days - 1)
    orders = services.shopify.orders(period_start, reporting_date)
    if len({o.currency for o in orders}) > 1:
        raise ValueError("Cannot combine sales in different currencies")
    net_merchandise_sales = sum((o.net_merchandise_sales for o in orders), start=0)
    return {
        "period_start": period_start,
        "period_end": reporting_date,
        "currency": orders[0].currency if orders else "AUD",
        "order_count": len(orders),
        "net_merchandise_sales": net_merchandise_sales,
        "average_order_value": net_merchandise_sales / len(orders) if orders else 0,
        "categories": shopify.sales_by_category(orders),
        "top_products": shopify.top_products(orders, limit=5),
        "daily": shopify.daily_sales(orders),
    }
