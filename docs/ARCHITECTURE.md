# Architecture

## Flow

```
cli run ─ config.load (toml + listed credentials from .env/env) ─ validate
  └ for each [[reports]]: run.build ─> Report ─> each output.deliver()
      reports/<id>.build(services, definition, reporting_date) ─> template_data
      xlsx.build(SHEETS, template_data)                ─> .xlsx attachment
      [prompt] claude.analyse(template_data JSON)      ─> Markdown, sanitised to plain tags in render.py
      outputs.FileDestination: HTML (logo as data URI) + xlsx;  outputs.EmailDestination: HTML (logo cid) via Mailgun
```

- **Service layer** (`services/`): typed pydantic results, Decimal money. Reports and Claude's tools call the same `DataSources` object, so they see the same data.
- **Mock mode**: `services/mock.py` subclasses each client and replaces only the HTTP call with a JSON file, so parsing and aggregation run as in production. `DataSources.reporting_date()` returns `as_of`, keeping mock reports stable.
- **Failures**: missing Claude auth/setup produces an ERROR and omission notice during a run but remains an error for `check`; one report failing doesn't stop the others; a failed Claude call drops only the analysis section, adds a notice and logs ERROR.
- **Execution boundary**: `execution.execute` runs data preparation, workbook creation, analysis and rendering in spawned workers. Each has a deadline; POSIX workers own a process group that is killed on completion/timeout to clean up descendants. Core failures become simple error emails; optional-stage failures become notices. Destinations are isolated and failure status is reflected in the exit code.
- **Delivery records**: SQLite locally; Actions uses per-delivery JSON receipts committed to a dedicated `report-deliveries` branch before sending. Records do not depend on prior runs/artifacts. Acceptance skips repeat sends, explicit rejection permits retry, and uncertain acceptance blocks resend. Existing local receipts can be imported without overwriting remote reservations.
- **Logging**: timestamped INFO lifecycle/acceptance messages, WARNING for retriable calls, ERROR tracebacks for failed stages. A run reference, report, stage and duration connect events without logging prompts or business data. Known credentials are redacted.
- **Sales comparisons**: `sales.compare_sales` computes equal inclusive calendar periods, zero-sales days, percentage changes, category comparisons, medians and high-sales days. The skill explains those results instead of doing arithmetic.
- **Outputs**: `outputs.py` (`run.ReportDestination` protocol); a new destination (PDF, Slack, S3) is a class with `deliver(Report)`.
- **Sales basis**: all Shopify totals use `priceAfterAllDiscountsBeforeTaxesSet` and `currentQuantity`: merchandise after discounts and refunded/removed items, excluding tax and shipping, grouped by order creation date. This is not cash received or refunds booked during the reporting period. Both order and nested line-item connections are paginated. Mixed currencies fail instead of being added.
- **Spreadsheet safety**: all string cells (including titles and headers) are explicitly stored as text, never formulas.
- **Reports**: `SOURCES` decides which credentials `check` demands. `SHEETS = [(sheet, template data key, columns)]` drives the one generic xlsx writer.
- **Auth per service**: Shopify Admin token; Xero client_credentials (Custom Connection, no refresh token to rotate) + tenant from `/connections`; Fishbowl login/logout token per call; Mailgun basic auth.

## Claude

Runs through the Claude Agent SDK (drives the bundled Claude Code CLI) so a subscription can be used. The agent gets the report data and only the read-only tools explicitly listed in that report's `claude_tools` configuration (default: none). Available credentials do not grant tool access. Each report's recipients must be authorised to see every listed tool's data. Invoice tools return only counts and amount due on period invoices, without customer names or invoice identifiers. Unknown tool names fail validation.

`claude/` is the single source for its restrictions; `claude.py` only adds auth, the tool server and paths:

| File | Holds |
|---|---|
| `settings.json` | `--settings`: `defaultMode: dontAsk`, allow `mcp__company` + listed skills, deny built-ins, bundled skills and built-in plugins off |
| `cli.toml` | options with no settings key: built-in tools (`Skill` only), `setting_sources = []`, flags (`restricted`, `strict-mcp-config`, `no-session-persistence`, `no-chrome`), turn and budget caps |
| `system-prompt.md` | system prompt |
| `skills/` | skills; each also needs `Skill(company-report:<name>)` in settings |
| `.claude-plugin/plugin.json` | makes `claude/` a plugin, passed as `--plugin-dir` |

Credentials:
- Data tools run **in-process** (SDK MCP server), so Shopify/Xero/Fishbowl/Mailgun credentials never leave report-clerk.
- The SDK merges `options.env` over the inherited environment and cannot remove variables, so the CLI is started via a generated wrapper (`cli_path`) that `execve`s it with an allowlist: `PATH`, `HOME`, locale, the SDK's own variables, and one Claude credential (OAuth token preferred over API key; both are never passed).
- Runs in an empty temp directory; no session transcripts written.

Why a plugin: with `setting_sources = []` and `--restricted`, the CLI ignores `~/.claude` and project `.claude/skills` (tested; `--add-dir` doesn't load skills either). `--plugin-dir` loads exactly one directory's skills, hooks and agents and nothing else, so `claude/` is the plugin root: whatever is put there is what the agent gets.

Verified on CLI 2.1.327 via the init message: tools = `Skill` + `mcp__company__*`, skills = only the repo's, permission mode `dontAsk`, MCP = only `company`.

## Not verified / known gaps

- Nothing has run against live Shopify, Xero, Fishbowl, Mailgun, or a Claude model turn (login expired). Coverage is unit tests plus the mock end-to-end run.
- Assumed from docs, untested: Shopify API version `2026-07` (orders older than 60 days need `read_all_orders`); Xero token request without `scope`; Fishbowl endpoints and first-login app approval; Mailgun sandbox limits.
- `disableBundledSkills`, `skillOverrides`, `enabledPlugins` for built-in plugins were found in the CLI binary, not in docs; they work on 2.1.327 only as far as the init message shows.
- The env wrapper keeps SDK variables by name (`CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SDK_READS_SESSION_STATE`, `CLAUDE_AGENT_SDK_*`); an SDK upgrade could add one it drops. `claude-agent-sdk` is pinned exactly; `test_cli_starts_with_only_the_configured_capabilities` checks the CLI's init (tools, skills, permission mode, MCP) on every test run.
- The CLI's built-in `telemetry` and `privacy` plugins stay enabled; managed (admin) policy settings would still apply.
- Claude's output is untrusted (data can carry prompt injection): `render.analysis_html` keeps only formatting tags, no links, images or attributes. Tool access is read-only.
- Tool errors are returned to Claude as text (exception message, may include request URLs, not tokens).
- `.xlsx` output not opened in Excel or Google Sheets; untested whether Sheets keeps the logo.
- The mock datasets were generated by a one-off script that isn't committed.

Delivery states are `unconfirmed` (reserved, including uncertain send outcomes) and `accepted` (Mailgun accepted the request, not proof of recipient delivery). Existing `pending`/`sent` receipts migrate automatically. Delivery IDs retain their original format so renamed report date fields do not cause resends.
