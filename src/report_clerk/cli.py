import argparse
import logging
import sys
from datetime import date
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
    run.add_argument('--out', type=Path, default=Path('out'))
    run.add_argument('--mock', type=Path, nargs='?', const=Path('mock'))
    run.add_argument('--no-ai', action='store_true')
    run.add_argument('--only', action='append', metavar='ID', help='run only this report id; repeatable')
    run.add_argument('--headed', action='store_true', help='show the browser for Xero web-app exports')

    xero = sub.add_parser('xero', help='try Xero web-app exports by hand').add_subparsers(dest='export', required=True)
    login = xero.add_parser('login', help='open the browser profile and log in if Xero asks')
    transactions = xero.add_parser('transactions', help='Account Transactions export: rows and total per account')
    transactions.add_argument('--from', dest='start', type=date.fromisoformat, required=True)
    transactions.add_argument('--to', dest='end', type=date.fromisoformat, required=True)
    transactions.add_argument('--account', action='append', help="label as in Xero's account picker; repeatable")
    transactions.add_argument('--report', default='warehouse_metrics', help='without --account: use this report\'s accounts')
    summary = xero.add_parser('summary', help='Executive Summary export: every labelled number for one month')
    summary.add_argument('--month', type=lambda s: date.fromisoformat(f'{s}-01'), required=True, help='YYYY-MM')
    for p in (login, transactions, summary):
        p.add_argument('--headed', action='store_true', help='show the browser')
    for p in (transactions, summary):
        p.add_argument('--save', type=Path, help='keep the downloaded xlsx files here')
        p.add_argument('--mock', type=Path, nargs='?', const=Path('mock'))
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
    browser = {'headed': True} if getattr(args, 'headed', False) else {}
    if getattr(args, 'save', None):
        browser['download_dir'] = args.save
    settings = settings.model_copy(update={'xero': settings.xero.model_copy(update=browser)})
    if args.command == 'xero':
        sys.exit(xero_command(args, settings, mock_dir))
    if args.command == 'check':
        errors = validate(settings) + analysis_errors(settings)
        for error in errors:
            log.error('config error: %s', error)
            print(f'config error: {error}', file=sys.stderr)
        if errors:
            sys.exit(1)
        print(f"ok: {len(settings.reports)} report(s): {', '.join(r.id for r in settings.reports)}")
        return

    from report_clerk.run import run_all

    if args.only:
        unknown = sorted(set(args.only) - {r.id for r in settings.reports})
        if unknown:
            log.error('--only: not in config: %s', ', '.join(unknown))
            sys.exit(1)
        settings = settings.model_copy(update={'reports': [r for r in settings.reports if r.id in args.only]})
    sys.exit(1 if run_all(settings, args.out, mock_dir=mock_dir, ai=not args.no_ai) else 0)


def xero_command(args, settings, mock_dir: Path | None) -> int:
    from report_clerk.services import DataSources

    if not mock_dir and settings.xero.missing():
        print('config error: missing xero.org_short_code (config.toml)', file=sys.stderr)
        return 1
    if args.export == 'login':
        from report_clerk.services import xero

        with xero.session(settings.xero) as xero:
            print(f'logged in: {xero.base}')
        return 0
    service = DataSources(settings, mock_dir).xero
    if args.export == 'summary':
        from report_clerk.reports.warehouse_metrics import month_end

        month = month_end(args.month)
        for label, value in service.executive_summaries([month])[month].items():
            print(f'{label}: {value}')
        return 0
    accounts = args.account
    if not accounts:
        from report_clerk import reports

        definition = next((r for r in settings.reports if r.id == args.report), None)
        module = reports.load(args.report)
        if definition is None or not hasattr(module, 'Options'):
            print(f'config error: no accounts: pass --account or list report {args.report!r} with options', file=sys.stderr)
            return 1
        accounts = [a for _, group in module.Options.model_validate(definition.options).groups() for a in group]
    rows = service.account_transactions(accounts, args.start, args.end)
    for account in accounts:
        lines = [t for t in rows if t.account == account]
        print(f'{account}: {len(lines)} rows, net {sum((t.amount for t in lines), 0):,.2f}')
    return 0


if __name__ == '__main__':
    main()
