import hashlib
import json
import logging
from pathlib import Path

from report_clerk import mailgun, render
from report_clerk.config import MailgunConfig
from report_clerk.delivery import DeliveryJournal
from report_clerk.run import Report

log = logging.getLogger(__name__)


class FileDestination:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir

    def deliver(self, report: Report) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{report.definition.id}.html").write_text(report.html(render.LOGO_DATA_URI))
        for name, body in report.attachments.items():
            (self.out_dir / name).write_bytes(body)


class EmailDestination:
    def __init__(self, config: MailgunConfig, journal_path: Path = Path('.state/deliveries.sqlite3'), journal=None):
        self.config = config
        self.journal_path = journal_path
        self.journal = journal

    def deliver(self, report: Report) -> None:
        recipients = report.definition.to or self.config.to
        period_end = report.template_data.get('period_end', report.template_data.get('as_of_date'))
        period_start = report.template_data.get('period_start', period_end)
        # Keep the persisted identity format stable across field renames.
        identity = {
            'report': report.definition.id,
            'since': str(period_start), 'until': str(period_end),
            'domain': self.config.domain, 'sender': self.config.sender,
            'recipients': sorted(set(recipients)),
        }
        if report.failed:
            identity['kind'] = 'failure'  # Error notifications must not suppress a later successful report.
        delivery_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        html = report.html(render.LOGO_CID)
        journal = self.journal or DeliveryJournal(self.journal_path)
        if not journal.reserve(delivery_id):
            log.info('report %s already emailed for this period and destination', report.definition.id)
            return
        try:
            message_id = mailgun.send(self.config, recipients, report.title, html,
                                     inline=[] if report.failed else [render.LOGO], attachments=report.attachments)
        except mailgun.Rejected:
            journal.mark_rejected(delivery_id)
            raise
        journal.mark_accepted(delivery_id)
        log.info('email accepted delivery_id=%s message_id=%s', delivery_id, message_id or '-')
