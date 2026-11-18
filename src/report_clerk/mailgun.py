from pathlib import Path

import requests

from report_clerk.config import MailgunConfig

class Rejected(RuntimeError):
    """Mailgun explicitly rejected this request; it is safe to retry after correction."""


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def send(
    config: MailgunConfig,
    to: list[str],
    subject: str,
    html: str,
    inline: list[Path] | None = None,
    attachments: dict[str, bytes] | None = None,
) -> str | None:
    # Free plan: sandbox domain, recipients must be authorised in Mailgun first.
    response = requests.post(
        f"{config.base_url}/v3/{config.domain}/messages",
        auth=("api", config.api_key),
        data={"from": config.sender, "to": to, "subject": subject, "html": html},
        files=[("inline", (p.name, p.read_bytes(), "image/png")) for p in inline or []]
        + [("attachment", (name, body, XLSX)) for name, body in (attachments or {}).items()],
        timeout=30,
    )
    if 400 <= response.status_code < 500 and response.status_code != 408:
        raise Rejected(f'Mailgun rejected request (HTTP {response.status_code})')
    response.raise_for_status()
    try:
        return response.json().get('id')
    except ValueError:
        return None
