"""Authenticated, read-only direct-method cash flow reporting."""
import calendar
from contextlib import contextmanager
import csv
from datetime import date, datetime, time, timedelta, timezone
import io
from zoneinfo import ZoneInfo

from flask import abort, make_response, request
from flask_admin import BaseView, expose
from sqlalchemy import text
from sqlalchemy.orm import Session

from pacioli.extensions import admin, db
from pacioli.functions.cash_flow import cash_flow_report, cash_flow_details
from pacioli.functions.cash_flow_evidence import cash_flow_context
from pacioli.views import PrivateModelView


SECTIONS = ('operating', 'investing', 'financing', 'unclassified',
            'adjustments', 'internal')


def report_dates(arguments, today=None):
    """Inclusive UI dates, with a trailing-year default through yesterday."""
    today = today or datetime.now(ZoneInfo('America/Chicago')).date()
    if not arguments.get('start') and not arguments.get('end'):
        end = today - timedelta(days=1)
        year = today.year - 1
        start = date(year, today.month,
                     min(today.day, calendar.monthrange(year, today.month)[1]))
        return start, end
    try:
        start = date.fromisoformat(arguments['start'])
        end = date.fromisoformat(arguments['end'])
        if (str(start) != arguments['start'] or str(end) != arguments['end'] or
                start > end or (end - start).days > 3660 or end >= date.max):
            raise ValueError()
        return start, end
    except (ValueError, TypeError, KeyError):
        raise ValueError('Choose valid start and end dates, in order, within ten years.')


@contextmanager
def cash_flow_session():
    """Own the transaction: Flask authentication may already use db.session.

    REPEATABLE READ fixes the financial and evidence snapshot across the report,
    coverage, and paginated detail queries. READ ONLY is enforced by PostgreSQL.
    No locks, cache refreshes, or financial writes belong in a report request.
    """
    with db.engine.connect() as connection:
        connection = connection.execution_options(isolation_level='REPEATABLE READ')
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        try:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout = '15000ms'"))
            yield session
        finally:
            session.close()
            if transaction.is_active:
                transaction.rollback()


def money(value):
    """Keep Decimal precision through display; never round through a float."""
    return format(value, ',.2f')


def _issue_text(issue):
    if isinstance(issue, str):
        return issue
    messages = {
        'invalid_transfer_evidence': 'A saved transfer match no longer validates; its cash movement remains unresolved.',
        'invalid_classification_evidence': 'A report classification has stale or incomplete evidence and was not applied.',
        'conflicting_classification': 'Conflicting report classifications were excluded pending review.',
        'cash_source_integrity': 'Some cash-source transactions do not agree with their booked journals; inspect the evidence checks.',
        'cash_account_without_feed': 'An active cash account has no connected source feed; its coverage is unverified.',
        'opening_coverage': 'There are export gaps between an adopted opening and this reporting period.',
        'opening_source_integrity': 'Source allocations before this period need review before beginning cash can be supported.',
        'opening_source_movement': 'Beginning recorded cash does not agree with the adopted opening plus source movements.',
        'statement_before_ledger_cutover': 'A historical statement predates the adopted opening and was not used to validate this ledger.',
    }
    return str(issue.get('message') or issue.get('detail') or
               messages.get(issue.get('kind')) or issue.get('kind') or issue)


def present_report(report, context, start, end):
    report.update(start=str(start), end=str(end))
    report['issues'] = [_issue_text(issue) for issue in context.get('issues', [])]
    report['coverage'], report['source_checks'] = [], []
    openings = {row['account_id']: row for row in context.get('opening_checks', [])}
    for row in context.get('coverage', []):
        opening = openings.get(row.get('account_id'), {})
        ranges = row['ranges']
        notes = []
        if row.get('complete'):
            notes.append('Imported exports cover every requested date.')
        else:
            notes.append('Complete USD export coverage is not established.')
            report['issues'].append('{}: export coverage or currency needs review.'.format(row['account']))
        if row.get('gaps'):
            notes.append('Missing dates: {}.'.format('; '.join('{} through {}'.format(*gap) for gap in row['gaps'])))
        if opening.get('covers_start'):
            notes.append('Statement-supported opening adopted at {}.'.format(opening['cutover']))
        else:
            report['issues'].append('{}: a supported opening covering the start date is not established.'.format(row['account']))
        if opening.get('coverage_gaps'):
            report['issues'].append('{}: export gaps before this period weaken the beginning cash balance.'.format(row['account']))
        if row.get('latest_statement_date'):
            notes.append('Latest statement close: {}.'.format(row['latest_statement_date']))
        report['coverage'].append(dict(name=row['account'],
            first_date=ranges[0][0] if ranges else None,
            last_date=ranges[-1][1] if ranges else None,
            missing_opening=not opening.get('covers_start'), detail=' '.join(notes)))
    for check in context.get('source_checks', []):
        valid = not (check['difference'] or check['unposted_sources'] or
                     check['invalid_source_ids'] or check['invalid_journal_ids'])
        report['source_checks'].append(dict(label=check['account'] + ' — source-to-journal',
            status='Agrees' if valid else 'Needs review',
            detail=('{} source transactions; {} booked entries. Source movement: {} USD; '
                    'booked movement: {} USD; difference: {} USD. {} unposted sources; '
                    '{} source issues; {} journal issues.').format(
                        check['source_count'], check['journal_count'], money(check['source_movement']),
                        money(check['booked_ofx_movement']), money(check['difference']),
                        len(check['unposted_sources']), len(check['invalid_source_ids']),
                        len(check['invalid_journal_ids']))))
    report['statement_disagreements'] = context.get('statement_disagreements', [])
    report['opening_checks'] = context.get('opening_checks', [])
    for check in report['statement_disagreements']:
        message = '{}: recorded cash differs from the {} statement close by {} USD.'.format(
            check['account'], check['end_date'], money(check['difference']))
        report['issues'].append(message)
        report['source_checks'].append(dict(label='Statement balance', status='Needs review', detail=message))
    if report.get('counts', {}).get('unknown_chart_journals'):
        report['issues'].append('Some journals lack complete chart metadata; their cash status or classification may be incomplete.')
    if not report['accounts']:
        report['issues'].append('No recorded cash accounts are available for this period.')
    report['issues'] = list(dict.fromkeys(report['issues']))
    report['external_cash_change'] = report['classified_cash_change']
    unresolved = next(s for s in report['sections'] if s['key'] == 'unclassified')
    report['provisional'] = bool(report['issues'] or unresolved['count'] or
                                 report['difference'] or report['adjustments'])
    return report


def _csv_text(value):
    value = str(value)
    # Labels can originate in user-editable chart metadata. Keep downloaded CSV
    # labels as data when opened by a spreadsheet.
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r')):
        return "'" + value
    return value


def statement_csv(report):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Consolidated statement of cash flows — direct method'])
    writer.writerow(['Start', report['start'], 'End (inclusive)', report['end'], 'Currency', 'USD'])
    writer.writerow(['Scope', 'Cash accounts recorded in Pacioli; unconnected accounts are not included'])
    writer.writerow(['Status', 'Provisional' if report['provisional'] else 'Recorded cash reconciles'])
    writer.writerow([])
    writer.writerow(['Activity', 'Category', 'Direction', 'Receipts USD', 'Payments USD', 'Net USD', 'Cash movements'])
    for section in report['sections']:
        for direction, rows in (('Inflow', section['inflows']), ('Outflow', section['outflows'])):
            for row in rows:
                writer.writerow([_csv_text(section['label']), _csv_text(row['label']), direction,
                                 format(row['receipts'], '.2f'), format(row['payments'], '.2f'),
                                 format(row['net'], '.2f'), row['count']])
        writer.writerow([_csv_text(section['label']), 'TOTAL', '',
                         format(section['receipts'], '.2f'), format(section['payments'], '.2f'),
                         format(section['net'], '.2f'), section['count']])
    writer.writerow([])
    writer.writerow(['Reconciliation', 'USD'])
    for label, key in [('Beginning cash', 'opening_cash'),
                       ('External cash flows including unresolved classifications', 'external_cash_change'),
                       ('Opening-balance and transfer-timing adjustments', 'adjustments'),
                       ('Change in recorded cash', 'net_cash_change'),
                       ('Ending cash', 'closing_cash'), ('Reconciliation difference', 'difference')]:
        writer.writerow([label, format(report[key], '.2f')])
    writer.writerow([])
    writer.writerow(['Cash account', 'Beginning USD', 'Movement USD', 'Ending USD'])
    for account in report['accounts']:
        writer.writerow([_csv_text(account['name']), format(account['opening'], '.2f'),
                         format(account['movement'], '.2f'), format(account['closing'], '.2f')])
    writer.writerow([])
    writer.writerow(['Notes'])
    for issue in report['issues']:
        writer.writerow([_csv_text(issue)])
    writer.writerow(['Gross receipts/payments; noncash card purchases excluded; card settlements are cash outflows.'])
    writer.writerow(['Internal cash transfers excluded from external cash flows; incomplete matches remain unresolved.'])
    response = make_response(output.getvalue())
    response.mimetype = 'text/csv'
    response.headers['Content-Disposition'] = (
        'attachment; filename="pacioli-cash-flows-{}-{}.csv"'.format(report['start'], report['end']))
    return response


class CashFlowsView(BaseView):
    is_accessible = PrivateModelView.is_accessible
    _handle_view = PrivateModelView._handle_view

    @expose('/')
    def index_view(self):
        try:
            start, end = report_dates(request.args)
            section = request.args.get('section', '')
            label = request.args.get('label', '')
            direction = request.args.get('direction', '')
            page = int(request.args.get('page', '1'))
            if (section and section not in SECTIONS or label and not section or
                    direction not in ('', 'inflow', 'outflow') or direction and not section or
                    len(label) > 240 or not 1 <= page <= 10000 or
                    request.args.get('export', '') not in ('', 'csv')):
                raise ValueError('Invalid report filter.')
        except ValueError as error:
            abort(400, description=str(error))
        begin = datetime.combine(start, time.min, tzinfo=timezone.utc)
        finish = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc)
        with cash_flow_session() as session:
            context = cash_flow_context(session, begin, finish)
            options = dict(transfer_kinds=context['transfer_kinds'],
                           overrides=context['classifications'])
            report = present_report(cash_flow_report(session, begin, finish, **options),
                                    context, start, end)
            details = (cash_flow_details(session, begin, finish, section=section,
                                        label=label or None, page=page,
                                        direction=direction or None, **options)
                       if section else None)
        response = (statement_csv(report) if request.args.get('export') == 'csv' else
                    make_response(self.render('cash_flow.html', report=report,
                        context=context, start=start, end=end, details=details,
                        selected_section=section, selected_label=label,
                        selected_direction=direction, money=money)))
        response.headers['Cache-Control'] = 'no-store, private'
        response.headers['Pragma'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response


admin.add_view(CashFlowsView(name='Cash Flows', endpoint='cash-flows',
                            category='Accounting'))
