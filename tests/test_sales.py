from datetime import date
from decimal import Decimal

import pytest

from report_clerk.services.shopify import compare_sales
from report_clerk.services.shopify import Order, LineItem


def order(day, net_merchandise_sales, currency='AUD'):
    return Order(name=f'order-{day}', created=date.fromisoformat(day), currency=currency,
                 net_merchandise_sales=Decimal(net_merchandise_sales),
                 lines=[LineItem(title='Tub', category='Bath', quantity=1, net_merchandise_sales=Decimal(net_merchandise_sales))])


def test_equal_periods_include_zero_sales_and_exclude_outside_dates():
    result = compare_sales([
        order('2026-10-31', '500'),  # before previous period
        order('2026-11-03', '20'),
        order('2026-11-06', '100'),
        order('2026-11-07', '900'),  # after current period
    ], date(2026, 11, 6), 3)
    assert result['current']['days'] == result['previous']['days'] == 3
    assert result['current']['period_start'] == date(2026, 11, 4)
    assert result['previous']['period_end'] == date(2026, 11, 3)
    assert result['current']['net_merchandise_sales'] == 100
    assert result['previous']['net_merchandise_sales'] == 20
    assert result['change']['percent'] == Decimal('400.0')
    assert result['categories'][0]['change']['amount'] == 80
    assert result['current']['median_daily_net_merchandise_sales'] == 0
    assert result['current']['high_sales_days'] == [{'day': date(2026, 11, 6), 'net_merchandise_sales': Decimal('100')}]
    assert result['change_without_high_sales_days']['amount'] == 0
    assert result['change_without_high_sales_days']['new'] is False
    assert result['meets_minimum_order_count'] is False


def test_zero_baseline_and_minimum_order_count():
    orders = [order('2026-11-05', '0') for _ in range(10)] + [order('2026-11-06', '10') for _ in range(10)]
    result = compare_sales(orders, date(2026, 11, 6), 1)
    assert result['meets_minimum_order_count'] is True
    assert result['change'] == {'amount': Decimal('100'), 'percent': None, 'new': True}
    assert result['current']['high_sales_days'] == []


def test_empty_sales_and_invalid_input():
    assert compare_sales([], date(2026, 11, 6), 1)['currency'] is None
    with pytest.raises(ValueError, match='days'):
        compare_sales([], date(2026, 11, 6), 0)
    with pytest.raises(ValueError, match='currencies'):
        compare_sales([order('2026-11-06', '10'), order('2026-11-06', '10', 'USD')], date(2026, 11, 6), 1)
