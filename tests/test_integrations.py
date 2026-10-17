from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import text

from pacioli.extensions import db, run_sql
from pacioli.functions import amazon_functions
from pacioli.models import (AmazonItems, AmazonOrders, Connections, JournalEntries,
                            Mappings, SecurityPrices, TrialBalances)


def test_amazon_csv_import_and_source_scoped_mapping(clean_ledger):
    suffix = uuid4().hex[:8]
    order_id = 'ORDER-' + suffix
    csv_data = (
        'Order Date,Order ID,Title,Category,Shipment Date,Item Total,Currency,Quantity\n'
        '2026-09-10,{order},Blue Widget,Office,2026-09-12,$12.34,USD,1\n'
        '2026-09-10,{order},Widget Refund,Office,2026-09-13,($5.00),USD,1\n'
    ).format(order=order_id)

    assert amazon_functions.import_amazon_csv(csv_data) == 2
    assert amazon_functions.import_amazon_csv(csv_data) == 0
    assert db.session.get(AmazonOrders, order_id).order_date == date(2026, 9, 10)
    assert db.session.query(AmazonItems).filter_by(order_id=order_id).count() == 2

    db.session.add(Mappings(source='ofx', keyword='Widget',
                            positive_credit_subaccount_id='Rent',
                            negative_debit_subaccount_id='Rent'))
    mapping = Mappings(source='amazon', keyword='Widget',
                       positive_debit_subaccount_id='Groceries',
                       positive_credit_subaccount_id='Amazon Payable',
                       negative_debit_subaccount_id='Amazon Payable',
                       negative_credit_subaccount_id='Groceries')
    db.session.add(mapping)
    db.session.commit()

    amazon_functions.apply_all_mappings()
    entries = (db.session.query(JournalEntries)
               .filter_by(transaction_source='amazon', mapping_id=mapping.id).all())
    assert len(entries) == 2
    debits = {entry.debit_subaccount: entry.functional_amount for entry in entries}
    assert debits == {'Groceries': Decimal('12.34'),
                      'Amazon Payable': Decimal('5.00')}
    amazon_functions.apply_all_mappings()
    assert db.session.query(JournalEntries).filter_by(transaction_source='amazon',
                                                       mapping_id=mapping.id).count() == 2


def test_amazon_mapping_does_not_partially_book_unsupported_currency(clean_ledger):
    suffix = uuid4().hex[:8]
    report = (
        'Order Date,Order ID,Title,Shipment Date,Item Total,Currency\n'
        '2026-09-10,{order},Mixed {suffix} USD,2026-09-12,10.00,USD\n'
        '2026-09-10,{order},Mixed {suffix} EUR,2026-09-11,5.00,EUR\n'
    ).format(order='ORDER-' + suffix, suffix=suffix)
    amazon_functions.import_amazon_csv(report)
    mapping = Mappings(source='amazon', keyword=suffix,
                       positive_debit_subaccount_id='Groceries',
                       positive_credit_subaccount_id='Amazon Payable')
    db.session.add(mapping)
    db.session.commit()

    with pytest.raises(ValueError, match='without a USD amount'):
        amazon_functions.apply_single_amazon_mapping(mapping.id)
    assert db.session.query(JournalEntries).filter_by(mapping_id=mapping.id).count() == 0
    assert db.session.query(TrialBalances).count() == 0


def test_ofx_sync_uses_the_selected_bank_account_history(app_context, monkeypatch):
    from pacioli.functions import ofx_functions
    for number, timestamp in [('11111111', '2025-01-10 12:00:00+00'),
                              ('22222222', '2026-01-10 12:00:00+00')]:
        identifier = run_sql(text(
            "INSERT INTO ofx.acctfrom (subclass, name) "
            "VALUES ('bankacctfrom', :name) RETURNING id"
        ), {'name': number}).scalar()
        run_sql(text(
            "INSERT INTO ofx.bankacctfrom (id, bankid, acctid, accttype) "
            "VALUES (:id, '123456789', :number, 'CHECKING')"
        ), {'id': identifier, 'number': number})
        run_sql(text(
            "INSERT INTO ofx.stmttrn (fitid, acctfrom_id, trntype, dtposted, trnamt) "
            "VALUES (:fitid, :id, 'DEBIT', :posted, -1)"
        ), {'fitid': 'TX-' + number, 'id': identifier, 'posted': timestamp})

    requested = {}

    def download(url, body):
        requested['body'] = body
        raise RuntimeError('network intentionally disabled')

    monkeypatch.setattr(ofx_functions, 'download', download)
    connection = Connections(type='Checking', routing_number='123456789',
                             account_number='11111111', url='https://bank.invalid',
                             org='bank', fid='1', user='test', password='test',
                             currency='USD')
    with pytest.raises(RuntimeError, match='network intentionally disabled'):
        ofx_functions.sync_ofx_connection(connection)
    assert b'<INCTRAN><DTSTART>20250110</DTSTART>' in requested['body']


def test_market_prices_update_existing_rows_without_network(app_context, monkeypatch):
    from pacioli.functions import investment_functions
    ticker = 'T' + uuid4().hex[:7].upper()
    run_sql(text(
        "INSERT INTO ofx.secinfo (subclass, uniqueidtype, uniqueid, secname, ticker) "
        "VALUES ('secinfo', 'CUSIP', :uniqueid, 'Test security', :ticker)"
    ), {'uniqueid': ticker, 'ticker': ticker})
    close = [10.0]

    def fake_download(*args, **kwargs):
        return pd.DataFrame([{'Adj Close': close[0], 'Close': close[0],
                              'High': close[0], 'Low': close[0],
                              'Open': close[0], 'Volume': 100.0}],
                            index=pd.to_datetime(['2026-09-20']))

    monkeypatch.setattr(investment_functions.yf, 'download', fake_download)
    investment_functions.update_ticker_prices()
    close[0] = 11.0
    investment_functions.update_ticker_prices()
    prices = db.session.query(SecurityPrices).filter_by(ticker=ticker).all()
    assert len(prices) == 1
    assert prices[0].adjusted_close == Decimal('11.0')
