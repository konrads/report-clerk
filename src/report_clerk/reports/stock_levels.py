from datetime import date

from report_clerk.config import ReportConfig
from report_clerk.services import DataSources

SOURCES = ("fishbowl",)
LOW_STOCK = 5
SHEETS = [("Stock", "stock", ["part_number", "description", "quantity", "uom"])]


def build(services: DataSources, definition: ReportConfig, reporting_date: date) -> dict:
    stock = sorted(services.fishbowl.inventory(), key=lambda s: s.quantity)
    return {
        "as_of_date": services.snapshot_date(),
        "stock": stock,
        "low_stock": [s for s in stock if s.quantity <= LOW_STOCK],
        "low_stock_threshold": LOW_STOCK,
    }
