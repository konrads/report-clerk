import json
import re
from datetime import date
from pathlib import Path

from report_clerk.services.fishbowl import FishbowlService
from report_clerk.services.shopify import ShopifyService
from report_clerk.services.xero import XeroService

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
        self.pnl = _load(mock_dir, "xero_profit_and_loss.json")
        self.invoices_body = _load(mock_dir, "xero_invoices.json")

    def _get(self, path: str, params: dict) -> dict:
        if path == "Reports/ProfitAndLoss":
            return self.pnl
        y, m, d = map(int, re.search(r"DateTime\((\d+),(\d+),(\d+)\)", params["where"]).groups())
        since = date(y, m, d).isoformat()
        page = [i for i in self.invoices_body["Invoices"] if i["DateString"][:10] >= since]
        return {"Invoices": page if params.get("page", 1) == 1 else []}


class MockFishbowl(FishbowlService):
    def __init__(self, mock_dir: Path):
        self.body = _load(mock_dir, "fishbowl_inventory.json")

    def _login(self) -> str:
        return "mock-token"

    def _logout(self, token: str) -> None:
        pass

    def _inventory_page(self, token: str, page: int) -> dict:
        return self.body
