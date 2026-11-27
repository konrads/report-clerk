from datetime import date
from decimal import Decimal

from report_clerk import config
from report_clerk.services import DataSources
from report_clerk.services.shopify import ShopifyService
from report_clerk.reports import sales_summary
from report_clerk.run import Report


def node(name, day, total, lines):
    return {
        "id": name,
        "name": name,
        "createdAt": f"{day}T10:00:00Z",
        "cancelledAt": None,
        "currentTotalPriceSet": {"shopMoney": {"amount": total, "currencyCode": "AUD"}},
        "lineItems": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {"title": t, "currentQuantity": q, "priceAfterAllDiscountsBeforeTaxesSet": {"shopMoney": {"amount": a, "currencyCode": "AUD"}}, "product": {"productType": c}}
                for t, c, q, a in lines
            ]
        },
    }


PAGE = {
    "orders": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [
            node("#1001", "2026-11-01", "8990.00", [("Cold Plunge Tub", "Ice Bath", 1, "8990.00")]),
            node("#1002", "2026-11-02", "17490.00", [("Barrel Sauna", "Sauna", 1, "14990.00"), ("Plunge Cover", "Ice Bath", 1, "2500.00")]),
            node("#1003", "2026-11-02", "4590.00", [("Trail E-Bike", "E-Bike", 1, "4590.00")]),
        ],
    }
}


def test_sales_summary(write_config, monkeypatch, tmp_path):
    monkeypatch.setattr(ShopifyService, "_graphql", lambda self, q, v: PAGE)
    settings = config.load(write_config('[[reports]]\nid = "sales_summary"\ntitle = "Sales"\n'))
    report = settings.reports[0]
    template_data = sales_summary.build(DataSources(settings), report, date(2026, 11, 18))

    assert template_data["net_merchandise_sales"] == Decimal("31070.00")
    assert [(c.category, c.order_count, c.net_merchandise_sales) for c in template_data["categories"]] == [
        ("Sauna", 1, Decimal("14990.00")),
        ("Ice Bath", 2, Decimal("11490.00")),
        ("E-Bike", 1, Decimal("4590.00")),
    ]
    assert [d.order_count for d in template_data["daily"]] == [1, 2]

    summary = Report(report, "Sales", template_data, "Saunas lead.").summary()
    assert ("Net merchandise sales", "AUD 31,070.00") in summary and ("Summary", "Saunas lead.") in summary


def test_sales_reconcile_after_discounts_refunds_and_line_pagination(monkeypatch):
    from report_clerk.config import ShopifyConfig
    from report_clerk.services.shopify import sales_by_category, daily_sales, top_products

    order = node('#1', '2026-11-01', '999.00', [('Discounted', 'A', 2, '80.00')] + [(f'Free item {i}', 'A', 1, '0.00') for i in range(49)])
    order['lineItems']['pageInfo'] = {'hasNextPage': True, 'endCursor': 'line-50'}
    extra = node('#1', '2026-11-01', '0', [('Remaining after refund', 'B', 1, '20.00'), ('Refunded', 'B', 0, '0.00')])
    calls = []

    def graphql(self, query, variables):
        calls.append(variables)
        if 'id' in variables:
            return {'order': {'lineItems': extra['lineItems']}}
        return {'orders': {'nodes': [order], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}

    monkeypatch.setattr(ShopifyService, '_graphql', graphql)
    orders = ShopifyService(ShopifyConfig()).orders(date(2026, 11, 1))
    assert calls[1] == {'id': '#1', 'cursor': 'line-50'}
    assert orders[0].net_merchandise_sales == Decimal('100.00')
    assert sum(c.net_merchandise_sales for c in sales_by_category(orders)) == Decimal('100.00')
    assert sum(p.net_merchandise_sales for p in top_products(orders)) == Decimal('100.00')
    assert daily_sales(orders)[0].net_merchandise_sales == Decimal('100.00')
    assert sum(line.quantity for line in orders[0].lines) == 52


def test_xlsx_external_strings_remain_text():
    from io import BytesIO
    from openpyxl import load_workbook
    from report_clerk.xlsx import build

    values = ['=HYPERLINK("https://example.com","click")', '+1', '-1', '@SUM(A1)', 'normal']
    body = build('=1+1', [('=label', '=value')], [('Data', 'rows', ['=column'])], {'rows': [{'=column': v} for v in values]})
    wb = load_workbook(BytesIO(body))
    assert wb['Summary']['A6'].data_type == 's' and wb['Summary']['B6'].data_type == 's'
    ws = wb['Data']
    assert ws['A3'].data_type == 's'
    assert ws['A5'].data_type == 's'
    for row, value in enumerate(values, 6):
        assert ws.cell(row, 1).value == value
        assert ws.cell(row, 1).data_type == 's'
