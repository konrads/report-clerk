from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from report_clerk.config import ReportConfig
from report_clerk.services import DataSources
from report_clerk.services.xero import Transaction

SOURCES = ("xero",)
SHEETS = [
    ("Metrics", "metric_rows", "metric_columns"),
    ("Transactions", "transaction_rows", ["group", "account", "date", "source", "description", "reference", "amount", "counted", "carrier"]),
]
EARLIEST = date(2000, 1, 1)  # start of the export when no months limit is set
OTHER = "Other"


class Options(BaseModel):
    # Account labels exactly as in Xero's account picker.
    model_config = ConfigDict(extra="forbid")

    months: int | None = Field(default=None, ge=1, le=600)  # completed months back; None = all history
    fulfilled_order_accounts: list[str]
    fulfilled_order_sources: list[str] = ["Receivable Invoice"]
    min_order_amount: Decimal = Decimal("0.01")  # lines at or below this amount are not orders
    cogs_accounts: list[str]
    freight_accounts: list[str]
    freight_carriers: dict[str, str] = {}  # carrier -> description prefix; the rest is "Other"
    rent_accounts: list[str]
    rent_contacts: list[str] = []  # description prefixes counted as warehouse rent; empty = all
    warehouse_expense_accounts: list[str]
    inventory_adjustment_accounts: list[str]
    average_order_value_label: str | None = None  # Executive Summary row; None skips that export

    @model_validator(mode="after")
    def _accounts_once(self):
        labels = [a for _, accounts in self.groups() for a in accounts]
        repeated = sorted({a for a in labels if labels.count(a) > 1})
        if repeated:
            raise ValueError(f"accounts listed in more than one group: {', '.join(repeated)}")
        return self

    def groups(self) -> list[tuple[str, list[str]]]:
        return [("fulfilled_orders", self.fulfilled_order_accounts), ("cogs", self.cogs_accounts),
                ("freight", self.freight_accounts), ("rent", self.rent_accounts),
                ("warehouse_expense", self.warehouse_expense_accounts), ("inventory_adjustments", self.inventory_adjustment_accounts)]


def month_end(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def month_ends(start: date, end: date) -> list[date]:
    out, d = [], start.replace(day=1)
    while d <= end:
        out.append(month_end(d))
        d = out[-1] + timedelta(days=1)
    return out


def first_of_month_back(end: date, months: int) -> date:
    index = end.year * 12 + end.month - months
    return date(index // 12, index % 12 + 1, 1)


def last_complete_month_end(reporting_date: date) -> date:
    return reporting_date if reporting_date == month_end(reporting_date) else reporting_date.replace(day=1) - timedelta(days=1)


def carrier(t: Transaction, options: Options) -> str:
    text = t.description.lower()
    return next((name for name, prefix in options.freight_carriers.items() if text.startswith(prefix.lower())), OTHER)


def counted(t: Transaction, group: str, options: Options) -> bool:
    if group == "fulfilled_orders":
        return t.source in options.fulfilled_order_sources and abs(t.amount) > options.min_order_amount
    if group == "rent" and options.rent_contacts:
        return any(t.description.lower().startswith(c.lower()) for c in options.rent_contacts)
    return True


def _ratio(a: Decimal | None, b: Decimal | None, scale: int = 1) -> Decimal | None:
    return None if a is None or not b else a / b * scale


def metrics(transactions: list[Transaction], months: list[date], options: Options,
            average_order_value: dict[date, Decimal | None]) -> list[tuple[str, dict[date, Decimal | None]]]:
    group_of = {a: g for g, accounts in options.groups() for a in accounts}
    total: dict[tuple, Decimal] = defaultdict(Decimal)
    order_lines: dict[date, int] = defaultdict(int)
    for t in transactions:
        group, m = group_of[t.account], month_end(t.date)
        if not counted(t, group, options):
            continue
        total[group, m] += t.amount
        if group == "fulfilled_orders":
            order_lines[m] += 1
        if group == "freight":
            total["carrier", carrier(t, options), m] += t.amount

    rows = []
    for m in months:
        fulfilled, lines = -total["fulfilled_orders", m], Decimal(order_lines[m])
        cogs, freight = total["cogs", m], total["freight", m]
        rent, expense = total["rent", m], total["warehouse_expense", m]
        variable, fixed = cogs + freight, rent + expense
        rows.append({
            "Inventory scraps and adjustments - $": total["inventory_adjustments", m],
            "Fulfilled orders - $": fulfilled,
            "Fulfilled orders - Qty (invoice lines)": lines,
            "Average revenue per sales order - $": average_order_value.get(m),
            "COGS - $": cogs,
            "Fulfilled orders $ less COGS - %": _ratio(fulfilled - cogs, fulfilled, 100),
            "Outbound freight - $": freight,
            **{f"Outbound freight - {name} - $": total["carrier", name, m] for name in [*options.freight_carriers, OTHER]},
            "Outbound freight / COGS - %": _ratio(freight, cogs, 100),
            "Variable cost (COGS + freight) - $": variable,
            "Warehouse rent - $": rent,
            "Warehouse expense - $": expense,
            "Fixed cost excl. wages (rent + expense) - $": fixed,
            "Average variable cost per fulfilled order - $": _ratio(variable, lines),
            "Average freight cost per fulfilled order - $": _ratio(freight, lines),
            "Average fixed cost excl. wages per fulfilled order - $": _ratio(fixed, lines),
            "Average fixed + variable cost per fulfilled order - $": _ratio(fixed + variable, lines),
        })
    names = list(rows[0]) if rows else []
    return [(name, {m: row[name] for m, row in zip(months, rows)}) for name in names]


def build(services: DataSources, definition: ReportConfig, reporting_date: date) -> dict:
    options = Options.model_validate(definition.options)
    end = last_complete_month_end(reporting_date)
    start = first_of_month_back(end, options.months) if options.months else EARLIEST
    accounts = [a for _, group in options.groups() for a in group]
    transactions = services.xero.account_transactions(accounts, start, end)
    if not options.months:
        start = min((t.date for t in transactions), default=end).replace(day=1)
    months = month_ends(start, end)

    average_order_value: dict[date, Decimal | None] = {}
    if options.average_order_value_label:
        summaries = services.xero.executive_summaries(months)
        average_order_value = {m: summaries[m].get(options.average_order_value_label) for m in months}

    table = metrics(transactions, months, options, average_order_value)
    group_of = {a: g for g, group in options.groups() for a in group}
    latest = {name: values[end] for name, values in table}

    def money(name: str) -> str:
        value = latest.get(name)
        return "–" if value is None else f"AUD {value:,.2f}"

    return {
        "figures": [
            (f"Fulfilled orders, {end:%b %Y}", money("Fulfilled orders - $")),
            ("Fulfilled order lines", f"{latest.get('Fulfilled orders - Qty (invoice lines)', 0):,.0f}"),
            ("COGS", money("COGS - $")),
            ("Outbound freight", money("Outbound freight - $")),
            ("Warehouse rent + expense", money("Fixed cost excl. wages (rent + expense) - $")),
            ("Average variable cost per fulfilled order", money("Average variable cost per fulfilled order - $")),
        ],
        "period_start": start,
        "period_end": end,
        "metric_columns": ["metric", *(f"{m:%Y-%m}" for m in months)],
        "metric_rows": [{"metric": name, **{f"{m:%Y-%m}": values[m] for m in months}} for name, values in table],
        "transaction_rows": [{
            "group": group_of[t.account], "account": t.account, "date": t.date, "source": t.source,
            "description": t.description, "reference": t.reference, "amount": t.amount,
            "counted": counted(t, group_of[t.account], options),
            "carrier": carrier(t, options) if group_of[t.account] == "freight" else "",
        } for t in transactions],
    }
