import json
import logging
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path
from typing import Protocol

from pydantic_core import to_jsonable_python

from report_clerk import claude, render, reports, xlsx
from report_clerk.config import ReportConfig, Settings
from report_clerk.execution import execute
from report_clerk.logging_setup import new_run, report_id, run_id, stage
from report_clerk.services import DataSources
from report_clerk.validate import validate, analysis_errors

log = logging.getLogger(__name__)


@dataclass
class Report:
    definition: ReportConfig
    title: str
    template_data: dict
    analysis: str
    attachments: dict[str, bytes]
    issues: list[str] = field(default_factory=list)
    failed: bool = False
    error_count: int = 0
    documents: dict[str, str] = field(default_factory=dict)

    def html(self, logo_src: str = render.LOGO_DATA_URI) -> str:
        if self.failed:
            return '<h1>Report failed</h1><p>' + escape(self.title) + '</p><ul>' + ''.join(
                '<li>' + escape(issue) + '</li>' for issue in self.issues
            ) + '</ul><p>Consult the ERROR logs using the run reference above.</p>'
        if logo_src in self.documents:
            return self.documents[logo_src]
        body = render.html(self.definition, self.template_data, self.analysis, logo_src)
        if self.issues:
            body += '<p><strong>Report notices</strong></p><ul>' + ''.join(
                '<li>' + escape(issue) + '</li>' for issue in self.issues
            ) + '</ul>'
        return body


class ReportDestination(Protocol):
    def deliver(self, report: Report) -> None: ...


def failure_report(definition: ReportConfig, reporting_date: date, issue: str) -> Report:
    return Report(definition, f'FAILED: {definition.title or definition.id}',
                  {'period_end': reporting_date}, '', {},
                  [f'{issue} Run reference: {run_id.get()}.'], failed=True)


def build(settings: Settings, services: DataSources, definition: ReportConfig, reporting_date: date, ai: bool = True) -> Report:
    with stage('data'):
        module = reports.load(definition.id)
        template_data = execute(module.build, services, definition, reporting_date, timeout=settings.execution.report_timeout)
    report = Report(definition, definition.title or definition.id, template_data, '', {})
    sheets = getattr(module, 'SHEETS', [])
    if sheets:
        try:
            with stage('spreadsheet'):
                content = execute(xlsx.build, report.title, sheets, template_data, render.LOGO,
                                  timeout=settings.execution.attachment_timeout)
                report.attachments[f'{definition.id}-{reporting_date.isoformat()}.xlsx'] = content
        except Exception:
            report.error_count += 1
            report.issues.append(f'Spreadsheet unavailable. Run reference: {run_id.get()}.')
    if definition.prompt and ai:
        try:
            with stage('analysis'):
                problems = analysis_errors(settings.model_copy(update={'reports': [definition]}))
                if problems:
                    raise RuntimeError('; '.join(problems))
                data = json.dumps(to_jsonable_python(template_data), indent=1)
                report.analysis = claude.analyse(settings.claude, services, definition.prompt, report.title,
                                                data, definition.claude_tools, timeout=settings.execution.analysis_timeout)
        except Exception:
            report.error_count += 1
            report.issues.append(f'Analysis unavailable. Run reference: {run_id.get()}.')
    elif definition.prompt and not ai:
        report.issues.append('Analysis was disabled or unavailable; see run logs.')
    with stage('render'):
        for logo in (render.LOGO_DATA_URI, render.LOGO_CID):
            report.documents[logo] = execute(report.html, logo, timeout=settings.execution.render_timeout)
    return report


def run_all(settings: Settings, destinations: list[ReportDestination], mock_dir: Path | None = None, ai: bool = True) -> int:
    token = new_run()
    failed = 0
    try:
        services = DataSources(settings, mock_dir)
        try:
            reporting_date = services.reporting_date()
        except Exception:
            log.exception('reporting date unavailable')
            # A bad dataset must still produce a failure email for each enabled report.
            reporting_date = date.today()
            date_failed = True
        else:
            date_failed = False
        log.info('run started reports=%d', len(settings.reports))
        if not settings.reports:
            log.error('no reports enabled')
            return 1
        for definition in settings.reports:
            report_token = report_id.set(definition.id)
            report_failed = False
            try:
                try:
                    if date_failed:
                        raise RuntimeError('Reporting date unavailable')
                    with stage('validation'):
                        problems = validate(settings.model_copy(update={'reports': [definition]}),
                                            email=False, mock=mock_dir is not None, ai=ai)
                        if problems:
                            raise ValueError('; '.join(problems))
                    report = build(settings, services, definition, reporting_date, ai)
                except Exception as error:
                    log.exception('report preparation failed; sending failure notification')
                    from report_clerk.logging_setup import redact
                    detail = redact(str(error).splitlines()[0][:400])
                    report = failure_report(definition, reporting_date, f'Report preparation failed: {detail}')
                report_failed = report.failed or report.error_count > 0
                for destination in destinations:
                    try:
                        with stage(type(destination).__name__):
                            destination.deliver(report)
                    except Exception:
                        report_failed = True
                        # Try all remaining destinations, even if local file output failed.
                        log.exception('destination failed destination=%s', type(destination).__name__)
                log.info('report completed outcome=%s', 'error' if report_failed else 'ok')
                failed += int(report_failed)
            finally:
                report_id.reset(report_token)
        log.log(logging.ERROR if failed else logging.INFO,
                'run completed reports=%d failed=%d', len(settings.reports), failed)
        return failed
    finally:
        run_id.reset(token)
