"""Home page. Figures are computed on request and rendered without external assets."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from flask import current_app, make_response
from flask_admin import AdminIndexView, expose
from flask_security import current_user


CHART_WIDTH, CHART_HEIGHT = 640, 220
CHART_PAD = dict(left=58, right=16, top=14, bottom=28)
ACTIVE_CHASE = ('queued', 'signing_in', 'waiting_for_mfa', 'choosing_accounts',
                'exporting', 'importing')
CHASE_ATTENTION = ('waiting_for_mfa', 'choosing_accounts', 'needs_attention')
STALE_IMPORT_DAYS = 7
# scripts/lan_backup.py records each successful run here; the timer runs daily.
LAN_BACKUP_DIR = Path(__file__).resolve().parents[2] / '.local' / 'lan-backup'
STALE_BACKUP_HOURS = 36


def dollars(amount):
    """Whole dollars with a true minus sign, for headline figures."""
    if amount is None:
        return '—'
    value = Decimal(amount).quantize(Decimal('1'))
    return ('−$' if value < 0 else '$') + '{:,}'.format(abs(value))


def compact(amount):
    value = float(amount)
    for divisor, suffix in ((1e6, 'M'), (1e3, 'k')):
        if abs(value) >= divisor:
            text = '{:.1f}'.format(abs(value) / divisor).rstrip('0').rstrip('.')
            return ('−$' if value < 0 else '$') + text + suffix
    return dollars(amount)


def month_name(period, style='%b'):
    return datetime.strptime(period, '%Y-%m').strftime(style)


def days_since(when, now):
    if when is None:
        return None
    if not isinstance(when, datetime):
        when = datetime(when.year, when.month, when.day, tzinfo=timezone.utc)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (now - when).days)


def lan_backup_age(now):
    """Hours since the last successful LAN backup; None when none is configured."""
    directory = Path(current_app.config.get('PACIOLI_LAN_BACKUP_DIR', LAN_BACKUP_DIR))
    if not (directory / 'repository').is_file():
        return None
    try:
        finished = datetime.fromisoformat((directory / 'last-success').read_text().strip())
    except (OSError, ValueError):
        return float('inf')
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return max(0.0, (now - finished).total_seconds() / 3600)


def nice_step(span, ticks=4):
    raw = span / ticks if span else 1.0
    magnitude = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1.0
    for multiple in (1, 2, 2.5, 5, 10):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10 * magnitude


def scale(values, height=CHART_HEIGHT, include_zero=False):
    """Map values to SVG y coordinates using rounded gridlines."""
    values = [float(value) for value in values] or [0.0]
    low, high = min(values), max(values)
    if include_zero:
        low, high = min(low, 0.0), max(high, 0.0)
    if low == high:
        low, high = low - 1, high + 1
    step = nice_step(high - low)
    low = step * (low // step)
    high = step * -(-high // step)
    top, bottom = CHART_PAD['top'], height - CHART_PAD['bottom']

    def y(value):
        return round(bottom - (float(value) - low) / (high - low) * (bottom - top), 1)
    ticks, tick = [], low
    while tick <= high + step / 2:
        ticks.append({'y': y(tick), 'label': compact(tick)})
        tick += step
    return y, ticks


def x_positions(count, width=CHART_WIDTH):
    left, right = CHART_PAD['left'], width - CHART_PAD['right']
    if count == 1:
        return [round((left + right) / 2, 1)]
    return [round(left + index * (right - left) / (count - 1), 1) for index in range(count)]


def net_worth_chart(series):
    y, ticks = scale([row['net_worth'] for row in series])
    xs = x_positions(len(series))
    points = [{'x': x, 'y': y(row['net_worth']), 'label': month_name(row['period']),
               'period': row['period'], 'value': row['net_worth']}
              for x, row in zip(xs, series)]
    line = ' '.join('{},{}'.format(p['x'], p['y']) for p in points)
    baseline = CHART_HEIGHT - CHART_PAD['bottom']
    area = 'M{},{} L{} L{},{} Z'.format(points[0]['x'], baseline, line.replace(' ', ' L'),
                                       points[-1]['x'], baseline)
    return {'points': points, 'line': line, 'area': area, 'ticks': ticks,
            'width': CHART_WIDTH, 'height': CHART_HEIGHT,
            'left': CHART_PAD['left'], 'right': CHART_WIDTH - CHART_PAD['right']}


def flow_chart(series):
    y, ticks = scale([value for row in series for value in (row['income'], row['spending'])],
                     include_zero=True)
    left, right = CHART_PAD['left'], CHART_WIDTH - CHART_PAD['right']
    slot = (right - left) / len(series)
    bar = min(18.0, slot * 0.34)
    zero = y(0)
    groups = []
    for index, row in enumerate(series):
        center = left + slot * (index + 0.5)
        bars = []
        for offset, key in ((-bar - 1, 'income'), (1, 'spending')):
            top = y(max(row[key], 0))
            bars.append({'kind': key, 'x': round(center + offset, 1), 'y': top,
                         'height': round(max(y(min(row[key], 0)) - top, 0), 1),
                         'value': row[key]})
        groups.append({'x': round(center, 1), 'label': month_name(row['period']),
                       'period': row['period'], 'bars': bars})
    return {'groups': groups, 'bar': round(bar, 1), 'zero': zero, 'ticks': ticks,
            'width': CHART_WIDTH, 'height': CHART_HEIGHT, 'left': left, 'right': right}


def attention(report, now, backup_age=None):
    """Ordered, linkable review tasks. Links only; nothing here changes the books."""
    from flask import url_for
    queue, items = report['queue'], []
    if backup_age is not None and backup_age >= STALE_BACKUP_HOURS:
        items.append(dict(
            tone='danger', count='!', title='LAN backup is overdue',
            detail=('No successful LAN backup is recorded.' if backup_age == float('inf') else
                    'The last successful LAN backup finished {:.0f} hours ago.'.format(backup_age))
                   + ' Check systemctl --user status pacioli-lan-backup on the workstation.'))
    chase = report['chase']
    if chase and chase['status'] in CHASE_ATTENTION:
        local = chase['mode'] in ('local_ai', 'local_preview')
        items.append(dict(tone='danger', count='!', title='Chase sync is waiting for you',
                          detail='A code, browser approval, or account choice is needed.',
                          href=url_for('connections.chase_local' if local else
                                       'connections.chase_browser'), action='Open Chase sync'))
    if report['unknown_accounts']:
        items.append(dict(tone='danger', count=len(report['unknown_accounts']),
                          title='Journal accounts missing from the chart',
                          detail='Statement totals leave them out until they are classified.',
                          href=url_for('subaccounts.index_view'), action='Review subaccounts'))
    if queue['uncategorized']:
        suspense = report['month_suspense']
        detail = 'Posted to Bookkeeping Suspense until a mapping or review assigns a category.'
        if suspense['entries']:
            detail = '{} from this month are not yet counted as spending or income.'.format(
                suspense['entries'])
        items.append(dict(tone='warning', count=queue['uncategorized'],
                          title='Transactions awaiting a category', detail=detail,
                          href=url_for('mappings.index_view'), action='Review mappings'))
    if queue['suggestions_ready']:
        items.append(dict(tone='info', count=queue['suggestions_ready'],
                          title='Local model suggestions to approve',
                          detail='Qwen proposals never post until you approve them.',
                          href=url_for('reconciliation.reviews'), action='Open merchant reviews'))
    if queue['unmatched_transfers']:
        items.append(dict(tone='warning', count=queue['unmatched_transfers'],
                          title='Transfers and payments to match',
                          detail='Each needs its counterpart leg before it leaves Transfer Clearing.',
                          href=url_for('reconciliation.transfers'), action='Match transfers'))
    imports = [row['imported_at'] for row in report['accounts'] if row['imported_at']]
    age = days_since(max(imports), now) if imports else None
    if age is not None and age >= STALE_IMPORT_DAYS:
        items.append(dict(tone='info', count='{}d'.format(age), title='Bank data is getting old',
                          detail='The most recent statement import was {} days ago.'.format(age),
                          href=url_for('connections.chase_browser'), action='Sync now'))
    if queue['open_months']:
        items.append(dict(tone='muted', count=queue['open_months'],
                          title='Month{} ready to close'.format('s' if queue['open_months'] > 1 else ''),
                          detail='Open since {}. Close with manage.py close_month once reconciled.'.format(
                              month_name(queue['first_open'], '%B %Y')),
                          href=url_for('balance-sheet.index_view', period_interval='YYYY-MM',
                                       period=queue['first_open']),
                          action='Review balance sheet'))
    return items


class HomeView(AdminIndexView):

    @expose('/')
    def index(self):
        context = dict(report=None, failed=False, now=datetime.now(timezone.utc).astimezone())
        if (current_user.is_authenticated and current_user.is_active and
                current_user.has_role('administrator')):
            from pacioli.extensions import db
            from pacioli.functions.dashboard import dashboard
            try:
                report = dashboard(db.session)
            except Exception:
                db.session.rollback()
                current_app.logger.exception('Home dashboard failed')
                context['failed'] = True
            else:
                context.update(
                    report=report, attention=attention(report, report['now'],
                                                       lan_backup_age(report['now'])),
                    net_worth_chart=net_worth_chart(report['series']),
                    flow_chart=flow_chart(report['series']),
                    chase_active=bool(report['chase'] and
                                      report['chase']['status'] in ACTIVE_CHASE))
        response = make_response(self.render(
            'admin/index.html', dollars=dollars, compact=compact, month_name=month_name,
            days_since=days_since, **context))
        response.headers['Cache-Control'] = 'private, no-store'
        return response
