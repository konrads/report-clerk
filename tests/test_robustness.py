import logging
from pathlib import Path

import pytest

from report_clerk.config import Settings, ReportConfig
from report_clerk.run import run_all

ROOT = Path(__file__).parents[1]


def test_failed_report_is_reported_and_logged(tmp_path, monkeypatch, caplog, capsys):
    def timeout(*a, **kw):
        raise TimeoutError('Shopify did not answer')
    monkeypatch.setattr('report_clerk.reports.sales_summary.build', timeout)
    settings = Settings(reports=[ReportConfig(id='sales_summary')])
    assert run_all(settings, tmp_path, mock_dir=ROOT / 'mock') == 1
    assert 'FAILED: SALES_SUMMARY' in capsys.readouterr().out
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_shopify_retries_throttling_and_fails_permanent_errors(monkeypatch):
    from report_clerk.services.shopify import ShopifyService
    from report_clerk.config import ShopifyConfig
    import requests
    class Response:
        status_code = 200
        def __init__(self, body): self.body = body
        def json(self): return self.body
        def raise_for_status(self): pass
    bodies = [{'errors': [{'extensions': {'code': 'THROTTLED'}}]}, {'data': {'orders': {}}}]
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: Response(bodies.pop(0)))
    monkeypatch.setattr('report_clerk.services.shopify.clock.sleep', lambda _: None)
    service = ShopifyService(ShopifyConfig())
    assert service._graphql('query', {}) == {'orders': {}}
    bodies.append({'errors': [{'extensions': {'code': 'ACCESS_DENIED'}}]})
    with pytest.raises(RuntimeError, match='ACCESS_DENIED'): service._graphql('query', {})


def test_tool_argument_and_repetition_bounds():
    import asyncio
    from report_clerk.services import DataSources
    from report_clerk.analysis.tools import build
    tool = build(DataSources(Settings(), ROOT / 'mock'), ['shopify_daily_sales'])[0]
    for args in ({'days': -1}, {'days': 1000000000}, {'days': True}, {'unknown': 1}):
        assert asyncio.run(tool.handler(args))['is_error']
    for _ in range(3):
        assert not asyncio.run(tool.handler({'days': 7})).get('is_error')
    assert asyncio.run(tool.handler({'days': 7}))['is_error']


def test_log_secrets_and_correlation_are_preserved():
    from report_clerk import logging_setup
    settings = Settings()
    settings.shopify.access_token = 'secret-token-for-test'
    logging_setup.register_secrets(settings)
    record = logging.LogRecord('test', logging.ERROR, '', 0, 'failure secret-token-for-test', (), None)
    text = logging_setup.SafeFormatter('%(message)s').format(record)
    assert 'secret-token-for-test' not in text and '[REDACTED]' in text
