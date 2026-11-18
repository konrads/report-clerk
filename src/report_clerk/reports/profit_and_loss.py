from datetime import date, timedelta

from report_clerk.config import ReportConfig
from report_clerk.services import DataSources
from report_clerk.services.xero import invoice_summary

SOURCES = ("xero",)
SHEETS = [("Profit and loss", "pnl_rows", ["section", "line", "amount"])]


def build(services: DataSources, definition: ReportConfig, reporting_date: date) -> dict:
    period_start = reporting_date - timedelta(days=definition.days - 1)
    pnl = services.xero.profit_and_loss(period_start, reporting_date)
    invoices = services.xero.invoices(period_start, reporting_date)
    return {
        "period_start": period_start,
        "period_end": reporting_date,
        "pnl": pnl,
        "pnl_rows": [
            *({"section": s.title, "line": line.name, "amount": line.amount} for s in pnl.sections for line in s.lines),
            {"section": "", "line": "Net Profit", "amount": pnl.net_profit},
        ],
        **invoice_summary(invoices),
    }
