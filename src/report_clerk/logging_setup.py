"""Application logs with known credentials redacted; prompts and report data are not logged."""
import logging

_secrets = set()


def register_secrets(settings):
    from report_clerk.config import CREDENTIALS
    for section, field in CREDENTIALS.values():
        value = getattr(getattr(settings, section), field)
        if value:
            _secrets.add(value)


def redact(text):
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    return text


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def configure(level='INFO'):
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logging.basicConfig(level=level, handlers=[handler])
