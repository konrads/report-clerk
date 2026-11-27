from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from functools import cached_property
from pathlib import Path

from report_clerk.config import Settings
from report_clerk.services import mock
from report_clerk.services.shopify import ShopifyService
from report_clerk.services.xero import XeroService

SOURCES = ("shopify", "xero")


class DataSources:
    # Shared by reports and analysis tools. With mock_dir, all sources read JSON datasets instead.
    def __init__(self, settings: Settings, mock_dir: Path | None = None):
        self.settings = settings
        self.mock_dir = mock_dir

    @cached_property
    def shopify(self) -> ShopifyService:
        return mock.MockShopify(self.mock_dir) if self.mock_dir else ShopifyService(self.settings.shopify, self.settings.timezone)

    @cached_property
    def xero(self) -> XeroService:
        return mock.MockXero(self.mock_dir) if self.mock_dir else XeroService(self.settings.xero)

    def configured(self) -> list[str]:
        if self.mock_dir:
            return list(SOURCES)
        return [s for s in SOURCES if not getattr(self.settings, s).missing()]

    def reporting_date(self) -> date:
        return mock.as_of(self.mock_dir) if self.mock_dir else datetime.now(ZoneInfo(self.settings.timezone)).date() - timedelta(days=1)

