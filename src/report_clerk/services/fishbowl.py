from decimal import Decimal

import requests
from pydantic import BaseModel

from report_clerk.config import FishbowlConfig


class StockLine(BaseModel):
    part_number: str
    description: str
    quantity: Decimal
    uom: str


class FishbowlService:
    # Fishbowl Advanced REST API; first login must be approved in the Fishbowl client.
    def __init__(self, config: FishbowlConfig):
        self.config = config
        self.base = config.url.rstrip("/")

    def _login(self) -> str:
        response = requests.post(
            f"{self.base}/api/login",
            json={
                "appName": self.config.app_name,
                "appDescription": "Company reporting",
                "appId": self.config.app_id,
                "username": self.config.username,
                "password": self.config.password,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["token"]

    def _logout(self, token: str) -> None:
        requests.post(f"{self.base}/api/logout", headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def _inventory_page(self, token: str, page: int) -> dict:
        response = requests.get(
            f"{self.base}/api/parts/inventory",
            params={"pageNumber": page, "pageSize": 100},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def inventory(self) -> list[StockLine]:
        token = self._login()
        try:
            lines, page = [], 1
            while page <= 500:
                body = self._inventory_page(token, page)
                lines += [
                    StockLine(
                        part_number=r["partNumber"],
                        description=r.get("partDescription", ""),
                        quantity=Decimal(str(r["quantity"])),
                        uom=r.get("uom", {}).get("abbreviation", ""),
                    )
                    for r in body["results"]
                ]
                if page >= body["totalPages"]:
                    return lines
                page += 1
            raise RuntimeError('Fishbowl inventory page limit exceeded')
        finally:
            self._logout(token)
