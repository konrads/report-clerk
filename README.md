# report-clerk

[![build](https://github.com/konrads/report-clerk/actions/workflows/build.yml/badge.svg)](https://github.com/konrads/report-clerk/actions/workflows/build.yml)

Business reports for a small retailer (ice baths, saunas, e-bikes), run by hand on a local machine. Each report pulls data from Shopify (sales), and Xero (accounts, exported from the web app), and writes one `.xlsx` per report to the output folder: a Summary sheet (period, key figures, optional Claude-written summary, issues) followed by the data sheets. The same summary is printed. No schedule: Xero web-app exports may need an interactive login.

**Why:** replace manual exports with one config-driven job; let Claude add commentary (trends, anomalies) while giving it read-only, credential-free access to the data.

The code avoids the company's name; branding is only in `company_logo.png` and the spreadsheet colours.

## Run

Python 3.12+, [uv](https://docs.astral.sh/uv/). macOS or Windows (Windows: `winget install astral-sh.uv`, then the same commands in PowerShell).

```
uv run pytest --cov                                           # fails under 90% coverage
uv run report-clerk check                                     # validate config, credentials, Claude setup
uv run playwright install chromium                            # once, for Xero web-app exports
uv run report-clerk run --mock --no-ai                        # mock data, no credentials; writes to out/
uv run report-clerk run [--no-ai] [--out DIR]                 # real run; default out/
uv run report-clerk run --only warehouse_metrics [--headed]   # one report; --headed shows the browser

# Xero web-app exports by hand (add --mock to use mock/, --save DIR to keep the xlsx)
uv run report-clerk xero login                                # log in once; the session stays in xero.profile_dir
uv run report-clerk xero transactions --from 2026-10-01 --to 2026-10-31 [--account "320 - Freight Cost"]
uv run report-clerk xero summary --month 2026-10              # every labelled number in the Executive Summary
```

`run` validates first and exits 1 on any config error or failed report; other reports still run.

### Local model (Ollama)

For free dev runs against a local model instead of Claude. Not wired in yet: the app still calls Claude; this is for the planned Pydantic AI switch. On macOS run Ollama natively, not in Docker (Docker on macOS has no GPU access).

```
brew install ollama
brew services start ollama          # or: ollama serve
ollama pull qwen3:4b                # ~2.5 GB; supports tool calling
ollama run qwen3:4b "say hi"        # smoke test
```

The OpenAI-compatible API is at `http://localhost:11434/v1`. Expect weaker tool use and summaries than Claude: fine for end-to-end checks, not for judging prompts.

## Config

- `config.toml`: non-secret settings and the `[[reports]]` list. A commented-out report doesn't run. Per report: `id`, `title`, `days`, optional `prompt` (adds a Claude summary to its Summary sheet), `timeout`, `options` (report-specific, e.g. `warehouse_metrics`: accounts, carriers, optional `months` limit).
- Credentials: env vars or `.env`. Only the names in `.env.example` (`config.CREDENTIALS`) are read; no other env var affects config.
- `claude/`: everything Claude may do (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#claude)).

Data sources: Shopify dev store + custom app; Xero web app, the only Xero source, used by `warehouse_metrics` (`[xero]`; logs in through a dedicated browser profile, asking for a login in a visible window when needed); Claude via subscription token (`CLAUDE_OAUTH_TOKEN` from `claude setup-token`), else API key, else local `claude` login.

## Layout

```
config.toml            settings + report list
.env.example           credential names
claude/                Claude Code setup and plugin root: settings, CLI flags, system prompt, skills/
mock/                  datasets in real API response shapes; `as_of` in dataset.json is "today"
src/report_clerk/
  cli.py, config.py, validate.py
  run.py               builds each report, writes its xlsx, prints its summary
  xlsx.py              the one spreadsheet writer (Summary sheet + SHEETS)
  services/            data sources behind one DataSources object, + mock subclasses
    shopify.py         Shopify API client and sales breakdowns
    browser.py         shared Playwright session for website-driven services: profile, login when needed, downloads
    xero.py            Xero web app: navigation, one class per Xero report (export + parse), XeroService
  reports/             one module per report: SOURCES, SHEETS, build() -> data incl. "figures"
  analysis/            Claude: agent.py (locked-down Claude Code agent), tools.py (read-only data tools)
  assets/              company_logo.png
tests/                 unit tests; mock-data end-to-end run; no network
docs/                  ARCHITECTURE.md: data flow, Claude restrictions, unverified items
.github/workflows/     build.yml: tests + mock run on push/PR to master; no scheduled run
```

Adding a report: `reports/<id>.py` + a `[[reports]]` entry. `check` fails if the module is missing.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data flow, the Claude restrictions, and what has not been verified.

Claude access is granted per report with `claude_tools = ["shopify_daily_sales", "shopify_sales_by_category", "shopify_top_products"]`. With no list, Claude can analyse the supplied report but cannot query additional data. Grant only tools whose results may be shared with that report's readers. Available names: `shopify_sales_comparison` (calculated period comparison) and those three Shopify tools.

Shopify net merchandise sales are calculated after discounts and refunded/removed items, excluding tax and shipping. Every breakdown uses this basis, grouped by order creation date; it does not represent payments or refunds posted in the reporting period.

A failed Claude summary is logged at ERROR and noted as an issue on the Summary sheet. A failed report still writes its xlsx with only the Summary sheet and the error. Any failed report makes the run exit nonzero; other reports still run.

`timezone` sets the business timezone. Everything runs in one process; stop a run with Ctrl-C. Playwright waits are bounded, and Claude has turn, cost, input/output size and tool-call limits. Tools validate arguments and reject repeated calls without progress. Logs include timestamp, severity and report id; known credentials are redacted. Use `--log-level DEBUG` for sanitized diagnostics; prompts and report data are not logged.

Report periods end on the last completed day in the business timezone. Shopify fetches bounded pages, retries throttling with bounded backoff, and counts orders retaining merchandise. `warehouse_metrics` covers completed months only.

No live integration has been run as part of local tests.
