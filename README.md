# report-clerk

[![build](https://github.com/konrads/report-clerk/actions/workflows/build.yml/badge.svg)](https://github.com/konrads/report-clerk/actions/workflows/build.yml)

Scheduled business reports for a small retailer (ice baths, saunas, e-bikes). Each report pulls data from Shopify (sales), Xero (accounts) and Fishbowl (stock), renders a branded HTML email with an `.xlsx` attachment, optionally adds a Claude-written analysis section, and sends it via Mailgun. Runs every weekday in GitHub Actions.

**Why:** replace manual exports with one config-driven job; let Claude add commentary (trends, anomalies) while giving it read-only, credential-free access to the data.

The code avoids the company's name; branding is only in `company_logo.png` and the template colours.

## Run

Python 3.12+, [uv](https://docs.astral.sh/uv/).

```
uv run pytest --cov                                           # fails under 90% coverage
uv run report-clerk check                                       # validate config, credentials, Claude setup
uv run report-clerk run --mock --no-email --no-ai --out out     # mock data, no credentials
uv run report-clerk run [--no-email] [--no-ai] [--out DIR]      # real run; emails each report
```

`run` validates first and exits 1 on any config error or failed report; other reports still run.

## Config

- `config.toml`: non-secret settings and the `[[reports]]` list. A commented-out report doesn't run. Per report: `id`, `title`, `days`, optional `prompt` (adds the Claude section), optional `to`.
- Credentials: env vars, `.env`, or GitHub secrets. Only the names in `.env.example` (`config.CREDENTIALS`) are read; no other env var affects config.
- `claude/`: everything Claude may do (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#claude)).

Data sources: Shopify dev store + custom app; Xero Custom Connection on the demo company (free); Fishbowl Advanced (self-hosted, no free tier, its report is commented out); Mailgun free sandbox (recipients must be authorised); Claude via subscription token (`CLAUDE_OAUTH_TOKEN` from `claude setup-token`), else API key, else local `claude` login.

## Layout

```
config.toml            settings + report list
.env.example           credential names
claude/                Claude Code setup and plugin root: settings, CLI flags, system prompt, skills/
mock/                  datasets in real API response shapes; `as_of` in dataset.json is "today"
src/report_clerk/
  services/            API clients (Shopify GraphQL, Xero, Fishbowl REST) + mock subclasses
  reports/             one module per report: SOURCES, SHEETS, build() -> template data
  templates/           one Jinja HTML template per report, plus base.html
  assets/              company_logo.png
  tools.py             Claude's read-only data tools (in-process MCP server)
  claude.py            runs the locked-down Claude Code agent
  run.py               builds each report (data, xlsx, Claude section)
  outputs.py           where built reports go: FileDestination, EmailDestination
  render.py, xlsx.py, mailgun.py, validate.py, config.py, cli.py
tests/                 unit tests; mock-data end-to-end run; no network
docs/                  ARCHITECTURE.md: data flow, Claude restrictions, unverified items
.github/workflows/     build.yml: tests + mock run on push/PR to master; report.yml: weekday run, delivery journal retained between runs; report artifacts on failure (3 days)
```

Adding a report: `reports/<id>.py` + `templates/<id>.html` + a `[[reports]]` entry. `check` fails if either file is missing.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data flow, the Claude restrictions, and what has not been verified.

Claude access is granted per report with `claude_tools = ["shopify_daily_sales", "shopify_sales_by_category", "shopify_top_products"]`. With no list, Claude can analyse the supplied report but cannot query additional data. Grant only tools whose results may be shared with that report's recipients. Available names: `shopify_sales_comparison` (calculated period comparison), those three Shopify tools, `xero_profit_and_loss`, `xero_invoice_summary` (count and amount due on period invoices only), and `fishbowl_inventory`.

Shopify net merchandise sales are calculated after discounts and refunded/removed items, excluding tax and shipping. Every breakdown uses this basis, grouped by order creation date; it does not represent payments or refunds posted in the reporting period.

Analysis and attachments are optional: failures are logged at ERROR and the report is emailed with an omission notice. Core report or rendering failures produce a plain failure email. Destinations run independently, so a file write failure does not prevent email. Any failed stage makes the run exit nonzero; other reports still run. Invalid/unreadable configuration or unavailable mail/storage credentials can prevent email, but are logged as errors.

`[execution]` sets the business timezone and hard deadlines for data preparation, attachments, Claude analysis and rendering. Blocking work runs in spawned processes; on POSIX, timeout cleanup kills their process groups, including Claude descendants. Claude also has turn, cost, input/output size and tool-call limits. Tools validate arguments and reject repeated calls without progress. Logs include timestamp, severity, run reference, report, stage, elapsed time and email acceptance ID. Use `--log-level DEBUG` for sanitized diagnostics; prompts and report data are not logged. Hard host termination cannot send a final error email.

Scheduled periods end on the last completed day in the business timezone. Stock snapshots use today's local date. Shopify fetches bounded pages, retries throttling with bounded backoff, and counts orders retaining merchandise. Xero summaries include authorised/paid invoices within both period boundaries and reject mixed currencies rather than adding them.

Local delivery receipts live in `.state/deliveries.sqlite3`; persist this directory for cron or containers. In Actions, receipts live on the repository's `report-deliveries` branch and are committed BEFORE sending. The workflow token needs Contents write permission and permission to create/update that branch. This state is independent of workflow runs and survives cancellation. Never delete the delivery state branch.

Explicit Mailgun rejection is recorded as `rejected`, allowing a later retry after correction. Uncertain sends remain `unconfirmed` and require checking Mailgun before retrying. `accepted` means the provider accepted the request, not proof of recipient delivery. Failure notifications use a separate identity, so they cannot suppress a later successful report.

For an existing Actions installation, restore its previous `.state/deliveries.sqlite3` before switching to the new workflow: it is imported without overwriting remote records. Reconcile any interrupted sends missing from that snapshot against Mailgun before enabling the new sender. Do not discard old receipts to bypass uncertainty. No live integration or Actions execution has been performed as part of local tests.
