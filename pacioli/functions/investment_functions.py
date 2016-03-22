import csv
from datetime import datetime, timedelta

from matplotlib.finance import fetch_historical_yahoo
from pacioli.models import SecurityPrices
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from manage import make_shell_context

context = make_shell_context()
db = context['db']
app = context['app']

from pacioli.views.ofx_views import CostBases


def update_ticker_prices():
    with app.app_context():
        for ticker, in db.session.query(CostBases.ticker).all():
            data = fetch_historical_yahoo(ticker, datetime.now() - timedelta(days=7), datetime.now())
            reader = csv.DictReader(data)
            for row in reader:
                new_record = SecurityPrices()
                for key in row:
                    row[key.lower().replace('adj close', 'adjusted_close')] = row.pop(key)
                for column in inspect(SecurityPrices).attrs:
                    if column.key == 'id':
                        continue
                    elif column.key == 'ticker':
                        setattr(new_record, column.key, ticker)
                    else:
                        setattr(new_record, column.key, row[column.key])
                try:
                    db.session.add(new_record)
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()

if __name__ == '__main__':
    update_ticker_prices()