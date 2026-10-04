"""Update stored daily security prices without the removed matplotlib.finance API."""

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf
from sqlalchemy import func

from pacioli import db
from pacioli import models
from pacioli.models import SecurityPrices


PRICE_COLUMNS = {
    'adjusted_close': 'Adj Close',
    'close': 'Close',
    'high': 'High',
    'low': 'Low',
    'open': 'Open',
    'volume': 'Volume',
}


def update_ticker_prices():
    securities = models.Securities
    tickers = [ticker for ticker, in db.session.query(securities.ticker)
               .filter(securities.ticker.isnot(None)).distinct().all()]
    for ticker in tickers:
        latest = (db.session.query(func.max(SecurityPrices.date))
                  .filter(SecurityPrices.ticker == ticker).scalar())
        start = latest - timedelta(days=7) if latest else date.today() - timedelta(days=365)
        data = yf.download(ticker, start=start, end=date.today() + timedelta(days=1),
                           auto_adjust=False, multi_level_index=False,
                           progress=False, threads=False)
        if data.empty:
            raise RuntimeError('No market prices returned for {}'.format(ticker))
        for timestamp, row in data.iterrows():
            if pd.isna(row.get('Adj Close')) or pd.isna(row.get('Close')):
                continue
            price_date = timestamp.date()
            price = (db.session.query(SecurityPrices)
                     .filter_by(ticker=ticker, date=price_date).one_or_none())
            if price is None:
                price = SecurityPrices(ticker=ticker, date=price_date)
                db.session.add(price)
            for model_field, data_field in PRICE_COLUMNS.items():
                value = row.get(data_field)
                setattr(price, model_field,
                        None if value is None or pd.isna(value) else Decimal(str(value)))
        db.session.commit()
