import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic_core import to_jsonable_python

from report_clerk import reports, xlsx
from report_clerk.analysis import agent
from report_clerk.config import ReportConfig, Settings
from report_clerk.logging_setup import redact
from report_clerk.services import DataSources
from report_clerk.validate import analysis_errors, validate

log = logging.getLogger(__name__)


@dataclass
class Report:
    definition: ReportConfig
    title: str
    data: dict
    analysis: str = ''
    issues: list[str] = field(default_factory=list)
    failed: bool = False

    def period(self) -> str:
        if 'period_start' in self.data:
            return f"{self.data['period_start'].isoformat()} to {self.data['period_end'].isoformat()}"
        return ''

    def summary(self) -> list[tuple[str, str]]:
        # First sheet of the workbook and the console output: period, figures, Claude's summary, issues.
        rows = [('Period', self.period()), *self.data.get('figures', [])]
        rows += [('Summary', self.analysis)] if self.analysis else []
        return rows + [('Issue', issue) for issue in self.issues]


def build(settings: Settings, services: DataSources, definition: ReportConfig, reporting_date: date, ai: bool = True) -> Report:
    report = Report(definition, definition.title or definition.id, reports.load(definition.id).build(services, definition, reporting_date))
    if definition.prompt and not ai:
        report.issues.append('Analysis was disabled; see run logs.')
    elif definition.prompt:
        try:
            problems = analysis_errors(settings.model_copy(update={'reports': [definition]}))
            if problems:
                raise RuntimeError('; '.join(problems))
            payload = json.dumps(to_jsonable_python(report.data), indent=1)
            report.analysis = agent.analyse(settings.claude, services, definition.prompt, report.title, payload, definition.claude_tools)
        except Exception:
            log.exception('analysis failed report=%s', definition.id)
            report.issues.append('Analysis unavailable; see run logs.')
            report.failed = True
    return report


def write(report: Report, reporting_date: date, out_dir: Path) -> Path:
    sheets = getattr(reports.load(report.definition.id), 'SHEETS', []) if 'figures' in report.data else []  # failed: Summary only
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'{report.definition.id}-{reporting_date.isoformat()}.xlsx'
    path.write_bytes(xlsx.build(report.title, report.summary(), sheets, report.data))
    return path


def show(report: Report, path: Path | None) -> None:
    print(f'\n{report.title.upper()}')
    for label, value in report.summary():
        print(f'  {label}: {value}')
    print(f'  File: {path}' if path else '  File: not written; see run logs')


def run_all(settings: Settings, out_dir: Path, mock_dir: Path | None = None, ai: bool = True) -> int:
    # Returns the number of reports that failed; one failure doesn't stop the others.
    if not settings.reports:
        log.error('no reports enabled')
        return 1
    services = DataSources(settings, mock_dir)
    try:
        reporting_date = services.reporting_date()
    except Exception:
        log.exception('reporting date unavailable')
        return len(settings.reports)
    failed = 0
    for definition in settings.reports:
        try:
            problems = validate(settings.model_copy(update={'reports': [definition]}), mock=mock_dir is not None, ai=ai)
            if problems:
                raise ValueError('; '.join(problems))
            report = build(settings, services, definition, reporting_date, ai)
        except Exception as error:
            log.exception('report failed report=%s', definition.id)
            report = Report(definition, f'FAILED: {definition.title or definition.id}', {'period_end': reporting_date},
                            issues=[f'Report failed: {redact(str(error).splitlines()[0][:400])}'], failed=True)
        try:
            path = write(report, reporting_date, out_dir)
        except Exception:
            log.exception('spreadsheet failed report=%s', definition.id)
            report.failed, path = True, None
        failed += report.failed
        show(report, path)
    log.log(logging.ERROR if failed else logging.INFO, 'run completed reports=%d failed=%d', len(settings.reports), failed)
    return failed
