from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from report_clerk.config import MailgunConfig, ReportConfig
from report_clerk.delivery import DeliveryJournal
from report_clerk.outputs import EmailDestination
from report_clerk.run import Report


def report():
    return Report(ReportConfig(id='sales_summary'), 'Sales', {'period_start': date(2026, 11, 1), 'period_end': date(2026, 11, 30)}, '', {})


def test_sent_email_is_not_resent_after_restart(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(Report, 'html', lambda *a: 'html')
    monkeypatch.setattr('report_clerk.mailgun.send', lambda *a, **kw: sent.append(a))
    config = MailgunConfig(domain='d', sender='s@d', to=['a@b'])
    path = tmp_path / 'receipts.sqlite3'
    EmailDestination(config, path).deliver(report())
    EmailDestination(config, path).deliver(report())
    assert len(sent) == 1
    changed = report()
    changed.definition.to = ['other@b']
    EmailDestination(config, path).deliver(changed)
    assert len(sent) == 2


def test_uncertain_email_requires_review_before_resending(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Report, 'html', lambda *a: 'html')
    def send(*a, **kw):
        calls.append(a)
        raise TimeoutError('response lost')
    monkeypatch.setattr('report_clerk.mailgun.send', send)
    config = MailgunConfig(domain='d', sender='s@d', to=['a@b'])
    mailbox = EmailDestination(config, tmp_path / 'receipts.sqlite3')
    with pytest.raises(TimeoutError):
        mailbox.deliver(report())
    with pytest.raises(RuntimeError, match='unconfirmed'):
        mailbox.deliver(report())
    assert len(calls) == 1


def test_concurrent_reservations_cannot_both_send(tmp_path):
    path = tmp_path / 'receipts.sqlite3'
    DeliveryJournal(path)
    def reserve(_):
        try:
            return DeliveryJournal(path).reserve('same-delivery')
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(reserve, range(2))).count(True) == 1


@pytest.mark.parametrize('legacy_status', ['sent', 'pending'])
@pytest.mark.parametrize('snapshot', [False, True])
def test_legacy_receipts_preserve_delivery_identity(tmp_path, monkeypatch, legacy_status, snapshot):
    import hashlib
    import json
    import sqlite3

    existing = report()
    if snapshot:
        existing.template_data = {'as_of_date': date(2026, 11, 30)}
    identity = {
        'report': 'sales_summary',
        'since': '2026-11-30' if snapshot else '2026-11-01',
        'until': '2026-11-30', 'domain': 'd', 'sender': 's@d', 'recipients': ['a@b'],
    }
    delivery_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    path = tmp_path / 'legacy.sqlite3'
    connection = sqlite3.connect(path)
    with connection:
        connection.execute('CREATE TABLE deliveries (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
        connection.execute('INSERT INTO deliveries VALUES (?, ?)', (delivery_id, legacy_status))
    connection.close()
    calls = []
    monkeypatch.setattr(Report, 'html', lambda *a: 'html')
    monkeypatch.setattr('report_clerk.mailgun.send', lambda *a, **kw: calls.append(a))
    destination = EmailDestination(MailgunConfig(domain='d', sender='s@d', to=['a@b']), path)
    if legacy_status == 'sent':
        destination.deliver(existing)
    else:
        with pytest.raises(RuntimeError, match='unconfirmed'):
            destination.deliver(existing)
    assert calls == []
    connection = sqlite3.connect(path)
    assert connection.execute('SELECT status FROM deliveries').fetchone()[0] == ('accepted' if legacy_status == 'sent' else 'unconfirmed')
    connection.close()
