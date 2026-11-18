import argparse
import logging
import os
import sys
from pathlib import Path

from report_clerk import config, logging_setup
from report_clerk.validate import validate, analysis_errors

log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(prog='report-clerk')
    parser.add_argument('--config', type=Path, default=config.DEFAULT_CONFIG)
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='validate config and credentials')
    run = sub.add_parser('run', help='run every report listed in the config')
    run.add_argument('--no-email', action='store_true')
    run.add_argument('--out', type=Path)
    run.add_argument('--mock', type=Path, nargs='?', const=Path('mock'))
    run.add_argument('--no-ai', action='store_true')
    args = parser.parse_args()
    logging_setup.configure(args.log_level)
    try:
        settings = config.load(args.config)
    except Exception as error:
        # Validation exceptions can contain secret input values; do not print their representation.
        log.error('configuration could not be loaded type=%s path=%s', type(error).__name__, args.config)
        sys.exit(1)
    logging_setup.register_secrets(settings)
    mock_dir = getattr(args, 'mock', None)
    if args.command == 'check':
        errors = validate(settings) + analysis_errors(settings)
        for error in errors:
            log.error('config error: %s', error)
            print(f'config error: {error}', file=sys.stderr)
        if errors:
            sys.exit(1)
        print(f"ok: {len(settings.reports)} report(s): {', '.join(r.id for r in settings.reports)}")
        return

    from report_clerk.delivery import GitHubDeliveryJournal
    from report_clerk.outputs import EmailDestination, FileDestination
    from report_clerk.run import run_all

    destinations = [FileDestination(args.out)] if args.out else []
    if not args.no_email:
        try:
            journal = None
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                journal = GitHubDeliveryJournal(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_TOKEN', ''))
                # If migrating an existing installation, restore its last known journal here first.
                if Path('.state/deliveries.sqlite3').is_file():
                    journal.import_local(Path('.state/deliveries.sqlite3'))
            destinations.append(EmailDestination(settings.mailgun, journal=journal))
        except Exception:
            log.exception('delivery storage initialization failed; email disabled')
            sys.exit(1)
    if not destinations:
        log.error('no report destinations selected; use --out or enable email')
        sys.exit(1)
    sys.exit(1 if run_all(settings, destinations, mock_dir=mock_dir, ai=not args.no_ai) else 0)


if __name__ == '__main__':
    main()
