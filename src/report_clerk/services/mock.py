import json
from datetime import date
from pathlib import Path

from report_clerk.services.shopify import ShopifyService
from report_clerk.services.xero import XeroService
from report_clerk.services.xero import AccountTransactions, long_date

# Stand-ins that replace only the HTTP calls, so parsing and aggregation run as for real.


def _load(mock_dir: Path, name: str) -> dict:
    return json.loads((mock_dir / name).read_text())


def as_of(mock_dir: Path) -> date:
    return date.fromisoformat(_load(mock_dir, "dataset.json")["as_of"])


class MockShopify(ShopifyService):
    def __init__(self, mock_dir: Path):
        self.nodes = _load(mock_dir, "shopify_orders.json")["orders"]

    def _graphql(self, query: str, variables: dict) -> dict:
        return {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.nodes}}



class MockXero(XeroService):
    def __init__(self, mock_dir: Path):
        self.transactions = _load(mock_dir, "xero_account_transactions.json")["accounts"]
        self.summaries = _load(mock_dir, "xero_executive_summary.json")["months"]

    def _account_transactions_rows(self, accounts: list[str], start: date, end: date) -> list[tuple]:
        rows = [("Account Transactions",), ("Demo Company",), (f"For the period {long_date(start)} to {long_date(end)}",), (), AccountTransactions.HEADER]
        for label in accounts:
            lines = [tuple(r) for r in self.transactions.get(label, []) if start.isoformat() <= r[0] <= end.isoformat()]
            if lines:
                rows += [(label.split(" - ", 1)[-1],), *((long_date(date.fromisoformat(r[0])), *r[1:]) for r in lines), ("Total",)]
        return rows

    def _executive_summary_rows(self, month_ends: list[date]) -> dict[date, list[tuple]]:
        return {m: [("Executive Summary",), (f"For the month ended {long_date(m)}",), *(tuple(r) for r in self.summaries.get(m.isoformat(), []))]
                for m in month_ends}

