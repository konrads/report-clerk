import asyncio
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest
from claude_agent_sdk import ResultMessage
from report_clerk import claude
from report_clerk.config import ClaudeConfig, ReportConfig, Settings
from report_clerk.services import DataSources

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('outcome', ['success', 'budget', 'empty', 'oversized'])
def test_analysis_results_have_explicit_success_or_failure(monkeypatch, outcome):
    @contextmanager
    def options(*args): yield object()
    async def query(**kwargs):
        if outcome == 'empty': return
        yield ResultMessage(subtype='error_max_budget_usd' if outcome == 'budget' else 'success',
                            duration_ms=1, duration_api_ms=1, is_error=outcome == 'budget',
                            num_turns=1, session_id='test', result='x' * 20_001 if outcome == 'oversized' else 'analysis')
    monkeypatch.setattr(claude, 'agent_options', options)
    monkeypatch.setattr(claude, 'query', query)
    call = claude._analyse(ClaudeConfig(), DataSources(Settings(), ROOT / 'mock'), 'request', 'title', '{}', [])
    if outcome == 'success': assert asyncio.run(call) == 'analysis'
    else:
        with pytest.raises((RuntimeError, ValueError)): asyncio.run(call)


def test_analysis_oversized_input_rejected_before_sdk():
    with pytest.raises(ValueError, match='input limit'):
        asyncio.run(claude._analyse(ClaudeConfig(), DataSources(Settings()), '', '', 'x' * 200_001, []))


def test_invoice_and_stock_examples_have_correct_scope():
    from report_clerk.reports import profit_and_loss, stock_levels
    sources = DataSources(Settings(), ROOT / 'mock')
    period = profit_and_loss.build(sources, ReportConfig(id='profit_and_loss'), date(2026, 11, 18))
    assert period['period_end'] == date(2026, 11, 18)
    assert period['invoice_currency'] == 'AUD'
    stock = stock_levels.build(sources, ReportConfig(id='stock_levels'), date(2000, 1, 1))
    assert stock['as_of_date'] == date(2026, 11, 18)


def test_render_failure_notification_is_escaped():
    from report_clerk.run import failure_report
    report = failure_report(ReportConfig(id='sales_summary', title='<script>'), date(2026, 11, 18), '<unsafe>')
    assert '<script>' not in report.html()
    assert '&lt;unsafe&gt;' in report.html()
