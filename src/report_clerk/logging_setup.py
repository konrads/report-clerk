"""Small, correlated application logs without prompts, credentials or customer data."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_secrets = set()


def register_secrets(settings):
    from report_clerk.config import CREDENTIALS
    import os
    for section, field in CREDENTIALS.values():
        value = getattr(getattr(settings, section), field)
        if value:
            _secrets.add(value)
    if os.environ.get('GITHUB_TOKEN'):
        _secrets.add(os.environ['GITHUB_TOKEN'])


def redact(text):
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    return text


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


run_id = ContextVar('run_id', default='-')
report_id = ContextVar('report_id', default='-')
stage_name = ContextVar('stage_name', default='-')


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.run_id = run_id.get()
        record.report_id = report_id.get()
        record.stage = stage_name.get()
        return True


def configure(level='INFO'):
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(SafeFormatter(
        '%(asctime)s %(levelname)s %(name)s run=%(run_id)s report=%(report_id)s stage=%(stage)s %(message)s'
    ))
    logging.basicConfig(level=level, handlers=[handler])


@contextmanager
def stage(name):
    token = stage_name.set(name)
    started = time.monotonic()
    log = logging.getLogger('report_clerk.execution')
    log.info('started')
    try:
        yield
    except Exception:
        log.exception('failed duration_seconds=%.3f', time.monotonic() - started)
        raise
    else:
        log.info('completed duration_seconds=%.3f', time.monotonic() - started)
    finally:
        stage_name.reset(token)


def new_run():
    return run_id.set(uuid4().hex[:12])
