import logging
import time
from pathlib import Path

import pytest

from report_clerk.config import Settings, ReportConfig, MailgunConfig
from report_clerk.execution import execute
from report_clerk.outputs import EmailDestination, FileDestination
from report_clerk.run import run_all

ROOT = Path(__file__).parents[1]


def test_hard_deadline_stops_blocking_worker():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        execute(time.sleep, 20, timeout=0.2)
    assert time.monotonic() - started < 3


def test_report_timeout_emails_error_and_logs_error(tmp_path, monkeypatch, caplog):
    def timeout(*a, **kw):
        raise TimeoutError('data deadline exceeded')
    monkeypatch.setattr('report_clerk.run.execute', timeout)
    sent = []
    monkeypatch.setattr('report_clerk.mailgun.send', lambda *a, **kw: sent.append(a) or 'message-id')
    settings = Settings(reports=[ReportConfig(id='sales_summary')])
    destination = EmailDestination(MailgunConfig(to=['test@example.com']), tmp_path / 'journal')
    assert run_all(settings, [destination], mock_dir=ROOT / 'mock') == 1
    assert sent[0][2].startswith('FAILED:')
    assert 'Report failed' in sent[0][3]
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_file_failure_does_not_block_email(tmp_path, monkeypatch):
    monkeypatch.setattr(FileDestination, 'deliver', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    sent = []
    monkeypatch.setattr('report_clerk.mailgun.send', lambda *a, **kw: sent.append(a) or 'message-id')
    settings = Settings(reports=[ReportConfig(id='sales_summary')])
    assert run_all(settings, [FileDestination(tmp_path), EmailDestination(MailgunConfig(), tmp_path / 'journal')], mock_dir=ROOT / 'mock', ai=False) == 1
    assert len(sent) == 1 and not sent[0][2].startswith('FAILED:')


def test_definite_rejection_can_retry(tmp_path, monkeypatch):
    from report_clerk.delivery import DeliveryJournal
    journal = DeliveryJournal(tmp_path / 'journal')
    assert journal.reserve('test')
    journal.mark_rejected('test')
    assert journal.reserve('test')
    journal.mark_accepted('test')
    assert journal.reserve('test') is False


def test_remote_journal_survives_runner_restarts_and_rejection():
    import base64
    import json
    from report_clerk.delivery import GitHubDeliveryJournal
    records = {}
    class Response:
        def __init__(self, status, body): self.status_code, self.body = status, body
        def json(self): return self.body
        def raise_for_status(self):
            if self.status_code >= 400: raise RuntimeError(f'HTTP {self.status_code}')
    class Session:
        def request(self, method, url, **kwargs):
            if '/git/ref/' in url: return Response(200, {})
            key = url.rsplit('/', 1)[-1]
            if method == 'GET':
                return Response(200, records[key]) if key in records else Response(404, {})
            body = kwargs['json']
            records[key] = {'content': body['content'], 'sha': str(len(records) + 1)}
            return Response(200, {})
    def journal():
        value = GitHubDeliveryJournal('owner/repo', 'fake-token')
        value.session = Session()
        return value
    key = 'a' * 64
    assert journal().reserve(key)
    with pytest.raises(RuntimeError, match='unconfirmed'): journal().reserve(key)
    journal().mark_rejected(key)
    assert journal().reserve(key)
    journal().mark_accepted(key)
    assert journal().reserve(key) is False
    assert json.loads(base64.b64decode(records[key + '.json']['content']))['status'] == 'accepted'


def test_mailgun_rejection_is_distinct_from_unknown_outcome(monkeypatch):
    from report_clerk import mailgun
    class Response:
        status_code = 400
    monkeypatch.setattr(mailgun.requests, 'post', lambda *a, **kw: Response())
    with pytest.raises(mailgun.Rejected):
        mailgun.send(MailgunConfig(), [], 'test', 'test')


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
    from report_clerk.tools import build
    tool = build(DataSources(Settings(), ROOT / 'mock'), ['shopify_daily_sales'])[0]
    for args in ({'days': -1}, {'days': 1000000000}, {'days': True}, {'unknown': 1}):
        assert asyncio.run(tool.handler(args))['is_error']
    for _ in range(3):
        assert not asyncio.run(tool.handler({'days': 7})).get('is_error')
    assert asyncio.run(tool.handler({'days': 7}))['is_error']


def test_mixed_invoice_currency_rejected():
    from datetime import date
    from report_clerk.services.xero import Invoice, invoice_summary
    rows = [Invoice(number='1', contact='test', date=date(2026, 1, 1), status='AUTHORISED',
                    total=1, amount_due=1, currency=c) for c in ['AUD', 'USD']]
    with pytest.raises(ValueError, match='multiple currencies'): invoice_summary(rows)


def test_log_secrets_and_correlation_are_preserved():
    from report_clerk import logging_setup
    settings = Settings()
    settings.shopify.access_token = 'secret-token-for-test'
    logging_setup.register_secrets(settings)
    record = logging.LogRecord('test', logging.ERROR, '', 0, 'failure secret-token-for-test', (), None)
    logging_setup.ContextFilter().filter(record)
    text = logging_setup.SafeFormatter('%(run_id)s %(report_id)s %(stage)s %(message)s').format(record)
    assert 'secret-token-for-test' not in text and '[REDACTED]' in text
