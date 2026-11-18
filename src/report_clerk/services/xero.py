from datetime import date
from decimal import Decimal, InvalidOperation

import requests
from pydantic import BaseModel

from report_clerk.config import XeroConfig

TOKEN_URL = "https://identity.xero.com/connect/token"
API_URL = "https://api.xero.com"


class ReportLine(BaseModel):
    name: str
    amount: Decimal


class ReportSection(BaseModel):
    title: str
    lines: list[ReportLine]
    total: Decimal | None = None


class ProfitAndLoss(BaseModel):
    from_date: date
    to_date: date
    sections: list[ReportSection]
    net_profit: Decimal | None = None


class Invoice(BaseModel):
    number: str
    contact: str
    date: date
    status: str
    total: Decimal
    currency: str
    amount_due: Decimal


class XeroService:
    # Uses a Custom Connection (client_credentials), free with the demo company.
    def __init__(self, config: XeroConfig):
        self.config = config
        self._session: requests.Session | None = None

    def _http(self) -> requests.Session:
        if self._session is None:
            token = requests.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.config.client_id, self.config.client_secret),
                timeout=30,
            )
            token.raise_for_status()
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {token.json()['access_token']}", "Accept": "application/json"})
            connections = session.get(f"{API_URL}/connections", timeout=30)
            connections.raise_for_status()
            session.headers["Xero-tenant-id"] = connections.json()[0]["tenantId"]
            self._session = session
        return self._session

    def _get(self, path: str, params: dict) -> dict:
        response = self._http().get(f"{API_URL}/api.xro/2.0/{path}", params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def profit_and_loss(self, from_date: date, to_date: date) -> ProfitAndLoss:
        body = self._get("Reports/ProfitAndLoss", {"fromDate": from_date.isoformat(), "toDate": to_date.isoformat()})
        return parse_profit_and_loss(body, from_date, to_date)

    def invoices(self, since: date, until: date | None = None) -> list[Invoice]:
        where = f'Type=="ACCREC" AND Date>=DateTime({since.year},{since.month},{since.day})'
        until = until or date.today()
        where += f' AND Date<=DateTime({until.year},{until.month},{until.day}) AND (Status=="AUTHORISED" OR Status=="PAID")'
        invoices, page = [], 1
        while page <= 500:
            batch = self._get("Invoices", {"where": where, "page": page})["Invoices"]
            invoices += [
                Invoice(
                    number=i.get("InvoiceNumber", ""),
                    contact=i["Contact"]["Name"],
                    date=date.fromisoformat(i["DateString"][:10]),
                    status=i["Status"],
                    total=Decimal(str(i["Total"])),
                    currency=i["CurrencyCode"],
                    amount_due=Decimal(str(i["AmountDue"])),
                )
                for i in batch
                if i['Status'] in ('AUTHORISED', 'PAID') and since <= date.fromisoformat(i['DateString'][:10]) <= until
            ]
            if len(batch) < 100:
                return invoices
            page += 1
        raise RuntimeError('Xero invoice page limit exceeded')


def _amount(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def parse_profit_and_loss(body: dict, from_date: date, to_date: date) -> ProfitAndLoss:
    sections, net_profit = [], None
    for row in body["Reports"][0]["Rows"]:
        if row["RowType"] != "Section":
            continue
        section = ReportSection(title=row.get("Title", ""), lines=[])
        for sub in row.get("Rows", []):
            name, value = sub["Cells"][0]["Value"], _amount(sub["Cells"][1]["Value"])
            if value is None:
                continue
            if name == "Net Profit":
                net_profit = value
            elif sub["RowType"] == "SummaryRow":
                section.total = value
            else:
                section.lines.append(ReportLine(name=name, amount=value))
        if section.lines:
            sections.append(section)
    return ProfitAndLoss(from_date=from_date, to_date=to_date, sections=sections, net_profit=net_profit)


def invoice_summary(invoices: list[Invoice]) -> dict:
    currencies = {i.currency for i in invoices}
    if len(currencies) > 1:
        raise ValueError('Invoice summary contains multiple currencies; group or convert before adding')
    return {'invoice_count': len(invoices), 'period_invoice_amount_due': sum((i.amount_due for i in invoices), Decimal(0)),
            'invoice_currency': next(iter(currencies), None)}
