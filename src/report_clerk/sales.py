"""Sales comparisons calculated before the report is passed to an analyst."""
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from report_clerk.services.shopify import Order, sales_by_category


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
