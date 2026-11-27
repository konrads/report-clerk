# Architecture

## Flow

```
cli run ─ config.load (toml + listed credentials from .env/env) ─ validate
  └ for each [[reports]]: run.build ─> Report
      reports/<id>.build(services, definition, reporting_date) ─> data
      [prompt] analysis.agent.analyse(data JSON)       ─> plain-text summary
      run.write: xlsx.build(Summary + SHEETS, data)    ─> --out/<id>-<date>.xlsx
      run.show                                         ─> the Summary rows on stdout
```

- **Service layer** (`services/`): typed pydantic results, Decimal money. Reports and Claude's tools call the same `DataSources` object, so they see the same data.
- **Mock mode**: `services/mock.py` subclasses each client and replaces only the HTTP call with a JSON file, so parsing and aggregation run as in production. `DataSources.reporting_date()` returns `as_of`, keeping mock reports stable.
- **Failures**: missing Claude auth/setup produces an ERROR and omission notice during a run but remains an error for `check`; one report failing doesn't stop the others; a failed Claude call drops only the analysis section, adds a notice and logs ERROR.
- **One process**: reports, Claude and the browser run in the CLI's own process; Ctrl-C stops a run. Playwright waits and Claude's turn and cost caps bound the slow parts. A failed report leaves a Summary-only workbook with the error; a failed analysis becomes an issue row. The exit code is nonzero if any report failed.
- **macOS and Windows**: the Xero login notification uses `osascript` on macOS and a PowerShell balloon on Windows, and is always printed too. Claude's CLI gets a trimmed environment through the exec wrapper on POSIX and through a temporarily trimmed `os.environ` on Windows (restored when the CLI exits). CI runs the tests on Ubuntu, macOS and Windows.
- **Logging**: timestamp, level, logger and message; ERROR with traceback for a failed report or analysis. Prompts and business data are not logged; known credentials are redacted.
- **Sales comparisons**: `services.shopify.compare_sales` computes equal inclusive calendar periods, zero-sales days, percentage changes, category comparisons, medians and high-sales days. The skill explains those results instead of doing arithmetic.
- **Sales basis**: all Shopify totals use `priceAfterAllDiscountsBeforeTaxesSet` and `currentQuantity`: merchandise after discounts and refunded/removed items, excluding tax and shipping, grouped by order creation date. This is not cash received or refunds booked during the reporting period. Both order and nested line-item connections are paginated. Mixed currencies fail instead of being added.
- **Spreadsheet safety**: all string cells (including titles and headers) are explicitly stored as text, never formulas.
- **Reports**: `SOURCES` decides which credentials `check` demands. `SHEETS = [(sheet, data key, columns)]` drives the one generic xlsx writer, after a Summary sheet; columns may name a data key for columns known only at run time. A module with an `Options` model validates its report's `options`.
- **Auth per service**: Shopify Admin token; Xero web app via a dedicated Playwright browser profile.

## Xero web-app exports

The only Xero source (the Xero API client was removed). Runs locally only. Reports are triggered by hand; there is no schedule, because Xero may ask for an interactive login.

- `services/browser.py` is the shared Playwright session for any service that drives a website: persistent profile (`BrowserConfig.profile_dir`), headless unless the site redirects to its login page (then a desktop notification and a visible window, up to 15 minutes), downloads into a temp dir or `download_dir`, screenshot to `screenshot_dir` on failure. A site is described by a `Site`: start URL, login-page pattern and a ready check.
- `services/xero.py` adds Xero's part: `XeroSession` (the `reporting.xero.com/!<org>` URLs, opening reports, Update and Export to Excel) and one class per Xero report (`AccountTransactions`, `ExecutiveSummary`), each with `export()` (fill in the report page, download Excel) and `parse()`: Account Transactions (header and period line checked, one section per account, AUD debit/credit) and Executive Summary (label -> first number on the row). `MockXero` returns rows in the same shape. `browser.py`, `XeroSession` and the `export()` steps are excluded from coverage because they need a live login.
- `reports/warehouse_metrics.py` rebuilds the Xero-sourced rows of the "AU Warehouse Operations Metrics" workbook per completed month: inventory scraps and adjustments, fulfilled orders $ and lines, COGS, outbound freight by carrier (description prefix), warehouse rent (landlord prefix), warehouse expense, average revenue per order (Executive Summary) and the ratios built from them. One Account Transactions export covers every account; `options.months` limits how far back, unset means from the first transaction. Executive Summary is one export per month.
- Not in the report (not from Xero exports): stock on hand, order cycle time (both in Fishbowl, not connected), wages, unfulfilled orders (deferred revenue rec), the manual warehouse inputs, orders shipped per carrier.
- Checked against the workbook's own raw rows, Jul 2024 to Jun 2027 (36 months): scraps/adjustments, COGS, Mainfreight and Aramex freight match every month; fulfilled orders $ 23/36 and lines 19/36, freight total 35/36, rent 35/36, expense 33/36. The rest of the gaps are the workbook's manual tags (per-line order flags, carrier reclasses, invoices marked "miscoded").

## Claude

Runs through the Claude Agent SDK (drives the bundled Claude Code CLI) so a subscription can be used. The agent gets the report data and only the read-only tools explicitly listed in that report's `claude_tools` configuration (default: none). Available credentials do not grant tool access. Everyone who reads the output must be authorised to see every listed tool's data. Claude has no Xero tools. Unknown tool names fail validation.

`claude/` is the single source for its restrictions; `analysis/agent.py` only adds auth, the tool server and paths:

| File | Holds |
|---|---|
| `settings.json` | `--settings`: `defaultMode: dontAsk`, allow `mcp__company` + listed skills, deny built-ins, bundled skills and built-in plugins off |
| `cli.toml` | options with no settings key: built-in tools (`Skill` only), `setting_sources = []`, flags (`restricted`, `strict-mcp-config`, `no-session-persistence`, `no-chrome`), turn and budget caps |
| `system-prompt.md` | system prompt |
| `skills/` | skills; each also needs `Skill(company-report:<name>)` in settings |
| `.claude-plugin/plugin.json` | makes `claude/` a plugin, passed as `--plugin-dir` |

Credentials:
- Data tools run **in-process** (SDK MCP server), so Shopify credentials never leave report-clerk.
- The SDK merges `options.env` over the inherited environment and cannot remove variables, so the CLI is started via a generated wrapper (`cli_path`) that `execve`s it with an allowlist: `PATH`, `HOME`, locale, the SDK's own variables, and one Claude credential (OAuth token preferred over API key; both are never passed).
- Runs in an empty temp directory; no session transcripts written.

Why a plugin: with `setting_sources = []` and `--restricted`, the CLI ignores `~/.claude` and project `.claude/skills` (tested; `--add-dir` doesn't load skills either). `--plugin-dir` loads exactly one directory's skills, hooks and agents and nothing else, so `claude/` is the plugin root: whatever is put there is what the agent gets.

Verified on CLI 2.1.327 via the init message: tools = `Skill` + `mcp__company__*`, skills = only the repo's, permission mode `dontAsk`, MCP = only `company`.

## Not verified / known gaps

- Nothing has run against live Shopify, the Xero web app, or a Claude model turn (login expired). Coverage is unit tests plus the mock end-to-end run.
- Assumed from docs, untested: Shopify API version `2026-07` (orders older than 60 days need `read_all_orders`); Xero web-app selectors, the Executive Summary date field and its "Average value of invoices" row, and how Account Transactions names its sections.
- `disableBundledSkills`, `skillOverrides`, `enabledPlugins` for built-in plugins were found in the CLI binary, not in docs; they work on 2.1.327 only as far as the init message shows.
- The env wrapper keeps SDK variables by name (`CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SDK_READS_SESSION_STATE`, `CLAUDE_AGENT_SDK_*`); an SDK upgrade could add one it drops. `claude-agent-sdk` is pinned exactly; `test_cli_starts_with_only_the_configured_capabilities` checks the CLI's init (tools, skills, permission mode, MCP) on every test run.
- The CLI's built-in `telemetry` and `privacy` plugins stay enabled; managed (admin) policy settings would still apply.
- Claude's output is untrusted (data can carry prompt injection): it is written as a text cell, never as a formula. Tool access is read-only.
- Tool errors are returned to Claude as text (exception message, may include request URLs, not tokens).
- `.xlsx` output not opened in Excel or Google Sheets; untested whether Sheets keeps the logo.
- The mock datasets were generated by a one-off script that isn't committed.

