import csv
from datetime import datetime, timedelta

from matplotlib.finance import fetch_historical_yahoo
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from pacioli import db
from pacioli.views.ofx_views import Securities
from pacioli.models import SecurityPrices


def update_ticker_prices():
    for ticker, in db.session.query(Securities.ticker).all():
        start_date = datetime.now() - timedelta(days=7)
        end_date = datetime.now()
        data = fetch_historical_yahoo(ticker, start_date, end_date)
        reader = csv.DictReader(data)
        for row in reader:
            new_record = SecurityPrices()
            for key in row:
                key_name = key.lower().replace('adj close', 'adjusted_close')
                row[key_name] = row.pop(key)
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
