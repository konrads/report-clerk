import re

from report_clerk import reports
from report_clerk.tools import TOOL_SOURCES
from report_clerk.claude import logged_in as claude_logged_in
from report_clerk.claude import setup_errors as claude_setup_errors
from report_clerk.config import ENV_NAMES, Settings


def validate(settings: Settings, email: bool = True, mock: bool = False, ai: bool = True) -> list[str]:
    errors = []
    if not settings.reports:
        errors.append("no reports enabled in config")

    ids = [r.id for r in settings.reports]
    errors += [f"report '{i}' listed more than once" for i in sorted({i for i in ids if ids.count(i) > 1})]

    needed: set[str] = set()
    for report in settings.reports:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", report.id):
            errors.append(f"report '{report.id}': id must be lowercase letters, digits and _")
            continue
        try:
            module = reports.load(report.id)
        except ModuleNotFoundError:
            errors.append(f"report '{report.id}': no module {reports.module_name(report.id)}")
            continue
        if not hasattr(module, "build") or not hasattr(module, "SOURCES"):
            errors.append(f"report '{report.id}': module must define SOURCES and build()")
            continue
        if not reports.template_exists(report.id):
            errors.append(f"report '{report.id}': no template templates/{reports.template_name(report.id)}")
        if not mock:
            needed.update(module.SOURCES)
        if report.prompt and ai:
            for name in report.claude_tools:
                if name not in TOOL_SOURCES:
                    errors.append(f"report '{report.id}': unknown Claude tool '{name}'")

    if email:
        needed.add("mailgun")
    for name in sorted(needed):
        for field in getattr(settings, name).missing():
            where = f"env {ENV_NAMES[name, field]}" if (name, field) in ENV_NAMES else "config.toml"
            errors.append(f"missing {name}.{field} ({where})")
    return errors


def analysis_errors(settings: Settings) -> list[str]:
    if not any(r.prompt for r in settings.reports):
        return []
    errors = claude_setup_errors(settings.claude.setup_dir)
    if not (settings.claude.oauth_token or settings.claude.api_key or claude_logged_in()):
        errors.append("no Claude auth: set CLAUDE_OAUTH_TOKEN or CLAUDE_API_KEY, or run claude auth login")
    return errors
