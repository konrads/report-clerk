"""Persistent email delivery receipts and reservations."""
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


class DeliveryJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, status TEXT NOT NULL)')
            # Preserve receipts written before the delivery-state names were clarified.
            connection.execute("UPDATE deliveries SET status = 'accepted' WHERE status = 'sent'")
            connection.execute("UPDATE deliveries SET status = 'unconfirmed' WHERE status = 'pending'")

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            with connection:
                yield connection

    def reserve(self, delivery_id: str) -> bool:
        # Commit before contacting Mailgun: a crash or ambiguous response must not cause a resend.
        with self._connect() as connection:
            inserted = connection.execute(
                'INSERT OR IGNORE INTO deliveries VALUES (?, ?)', (delivery_id, 'unconfirmed')
            ).rowcount
            if inserted:
                return True
            status = connection.execute('SELECT status FROM deliveries WHERE id = ?', (delivery_id,)).fetchone()[0]
            if status == 'rejected':
                connection.execute('UPDATE deliveries SET status = ? WHERE id = ?', ('unconfirmed', delivery_id))
                return True
            if status == 'accepted':
                return False
            raise RuntimeError(f'Delivery {delivery_id} is unconfirmed; confirm its status with Mailgun before retrying')

    def mark_accepted(self, delivery_id: str) -> None:
        with self._connect() as connection:
            connection.execute('UPDATE deliveries SET status = ? WHERE id = ?', ('accepted', delivery_id))

    def mark_rejected(self, delivery_id: str) -> None:
        with self._connect() as connection:
            connection.execute('UPDATE deliveries SET status = ? WHERE id = ?', ('rejected', delivery_id))


class GitHubDeliveryJournal:
    """Durable per-delivery receipts, committed before sending; independent of Actions runs."""
    def __init__(self, repository: str, token: str, branch: str = 'report-deliveries'):
        import requests
        if not repository or not token:
            raise ValueError('GitHub delivery storage requires repository and token')
        self.base = f'https://api.github.com/repos/{repository}'
        self.branch = branch
        self.session = requests.Session()
        self.session.headers.update({'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
        self.ready = False

    def _request(self, method, path, **kwargs):
        response = self.session.request(method, self.base + path, timeout=10, **kwargs)
        if response.status_code not in (404, 409, 422):
            response.raise_for_status()
        return response

    def _prepare(self):
        if self.ready:
            return
        reference = self._request('GET', f'/git/ref/heads/{self.branch}')
        if reference.status_code == 404:
            repository = self._request('GET', '').json()
            source = self._request('GET', f'/git/ref/heads/{repository["default_branch"]}').json()
            created = self._request('POST', '/git/refs', json={
                'ref': f'refs/heads/{self.branch}', 'sha': source['object']['sha']})
            if created.status_code == 422:  # Another runner may have created the branch.
                self._request('GET', f'/git/ref/heads/{self.branch}').raise_for_status()
            else:
                created.raise_for_status()
        else:
            reference.raise_for_status()
        self.ready = True

    def _read(self, delivery_id):
        import base64
        import json
        import re
        if not re.fullmatch('[a-f0-9]{64}', delivery_id):
            raise ValueError('Invalid delivery ID')
        self._prepare()
        response = self._request('GET', f'/contents/.delivery/{delivery_id}.json', params={'ref': self.branch})
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        value = response.json()
        return json.loads(base64.b64decode(value['content'])), value['sha']

    def _write(self, delivery_id, status, sha):
        import base64
        import json
        body = {'message': f'Delivery {delivery_id}: {status}', 'branch': self.branch,
                'content': base64.b64encode(json.dumps({'status': status}).encode()).decode()}
        if sha:
            body['sha'] = sha
        response = self._request('PUT', f'/contents/.delivery/{delivery_id}.json', json=body)
        response.raise_for_status()  # A conflict or ambiguous response never permits sending.

    def reserve(self, delivery_id):
        value, sha = self._read(delivery_id)
        if value and value['status'] == 'accepted':
            return False
        if value and value['status'] != 'rejected':
            raise RuntimeError(f'Delivery {delivery_id} is unconfirmed; check Mailgun before retrying')
        self._write(delivery_id, 'unconfirmed', sha)
        return True

    def _mark(self, delivery_id, status):
        value, sha = self._read(delivery_id)
        if not value or value['status'] != 'unconfirmed':
            raise RuntimeError(f'Delivery {delivery_id} has no active reservation')
        self._write(delivery_id, status, sha)

    def mark_accepted(self, delivery_id):
        self._mark(delivery_id, 'accepted')

    def mark_rejected(self, delivery_id):
        self._mark(delivery_id, 'rejected')

    def import_local(self, path: Path):
        """One-time migration; never replace newer remote reservations."""
        local = DeliveryJournal(path)
        with local._connect() as connection:
            rows = connection.execute('SELECT id, status FROM deliveries').fetchall()
        for delivery_id, status in rows:
            value, sha = self._read(delivery_id)
            if value is None:
                self._write(delivery_id, status, sha)
