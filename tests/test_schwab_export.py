"""Schwab export parsers and multi-account switching, on synthetic files."""
from datetime import date, time
from decimal import Decimal
import json

import pytest

from pacioli.functions.brokerage_accounts import tax_treatment_for
from pacioli.functions.schwab_export import parse_positions_csv, parse_transactions_json


D = Decimal
TRANSACTIONS = {
    'FromDate': '09/30/2022', 'ToDate': '09/29/2026', 'TotalTransactionsAmount': '$1,110.44',
    'TotalFeesAndCommAmount': '$0.95',
    'BrokerageTransactions': [
        {'Date': '01/05/2026', 'Action': 'Funds Received', 'Symbol': '', 'Description': 'FIXTURE DEPOSIT',
         'Quantity': '', 'Price': '', 'Fees & Comm': '', 'Amount': '$2,000.00', 'AcctgRuleCd': '1'},
        {'Date': '01/06/2026 as of 01/05/2026', 'Action': 'Buy', 'Symbol': 'AAA', 'Description': 'FIXTURE FUND',
         'Quantity': '9.5', 'Price': '$100.00', 'Fees & Comm': '$0.95', 'Amount': '-$950.95', 'AcctgRuleCd': '1'},
        {'Date': '02/01/2026', 'Action': 'Bond Interest', 'Symbol': '99999ZZZ9', 'Description': 'FIXTURE BOND',
         'Quantity': '', 'Price': '', 'Fees & Comm': '', 'Amount': '$61.39', 'AcctgRuleCd': '1'},
        {'Date': '02/02/2026', 'Action': 'Journaled Shares', 'Symbol': 'AAA', 'Description': 'FIXTURE JOURNAL',
         'Quantity': '1', 'Price': '', 'Fees & Comm': '', 'Amount': '', 'AcctgRuleCd': '1'}]}
POSITIONS = ('"Positions for account Fixture Brokerage ...321 as of 04:00 PM ET, 2026/09/29"\n\n'
             '"Symbol","Description","Qty (Quantity)","Price","Price Chng $ (Price Change $)","Price Chng % (Price Change %)",'
             '"Mkt Val (Market Value)","Day Chng $ (Day Change $)","Day Chng % (Day Change %)","Cost Basis",'
             '"Gain $ (Gain/Loss $)","Gain % (Gain/Loss %)","Reinvest?","Reinvest Capital Gains?","Asset Type",\n'
             '"AAA","FIXTURE FUND","10","110.00","1.00","0.9%","$1,100.00","$10.00","0.9%","$1,000.00","$100.00","10%",'
             '"Yes","Yes","ETFs & Closed End Funds",\n'
             '"Cash & Cash Investments","--","--","--","--","--","$50.00","$0.00","0%","--","--","--","--","--",'
             '"Cash and Money Market",\n'
             '"Positions Total","","--","--","--","--","$1,150.00","$10.00","0.9%","$1,000.00","$100.00","10%","--","--","--",\n')


def test_transactions_json():
    parsed = parse_transactions_json(json.dumps(TRANSACTIONS).encode('utf-8-sig'))
    assert (parsed['window_start'], parsed['window_end'], parsed['total']) == (date(2022, 9, 30), date(2026, 9, 29),
                                                                               D('1110.44'))
    buy = parsed['rows'][1]
    assert (buy['posted_on'], buy['as_of'], buy['quantity'], buy['price'], buy['fees'], buy['amount']) == (
        date(2026, 1, 6), date(2026, 1, 5), D('9.5'), D('100.00'), D('0.95'), D('-950.95'))
    assert parsed['rows'][0]['symbol'] is None and parsed['rows'][3]['amount'] is None
    assert sum(r['amount'] for r in parsed['rows'] if r['amount']) == parsed['total']


@pytest.mark.parametrize('damage', ['missing_field', 'bad_amount', 'not_json'])
def test_transactions_json_rejects_damage(damage):
    data = json.loads(json.dumps(TRANSACTIONS))
    if damage == 'missing_field':
        del data['BrokerageTransactions'][0]['Amount']
    elif damage == 'bad_amount':
        data['BrokerageTransactions'][0]['Amount'] = '$2,000.001'
    raw = b'{not json' if damage == 'not_json' else json.dumps(data).encode()
    with pytest.raises(ValueError):
        parse_transactions_json(raw)


def test_positions_csv():
    parsed = parse_positions_csv(POSITIONS.encode(), expected_digits='321')
    assert parsed['market_date'] == date(2026, 9, 29) and parsed['as_of'].time() == time(16, 0)
    assert parsed['end_of_day'] and parsed['total'] == D('1150.00')
    holding, cash = parsed['lines']
    assert (holding['symbol'], holding['quantity'], holding['market_value'], holding['cost_basis']) == (
        'AAA', D('10'), D('1100.00'), D('1000.00'))
    assert cash['is_cash'] and cash['symbol'] is None and cash['market_value'] == D('50.00')
    with pytest.raises(ValueError, match='different account'):
        parse_positions_csv(POSITIONS.encode(), expected_digits='999')


@pytest.mark.parametrize('label, treatment', [('Contributory', 'retirement'), ('Roth Contributory IRA', 'retirement'),
                                              ('Inherited IRA from Someone', 'retirement'), ('Individual', 'taxable'),
                                              ('Brokerage', 'taxable'), ('Joint Tenant', 'taxable')])
def test_tax_treatment_from_account_type(label, treatment):
    assert tax_treatment_for(label) == treatment


def test_account_switching_is_verified_in_page(tmp_path):
    from pacioli.functions.schwab_local import AccountCollector

    class Bridge:
        def __init__(self):
            self.selected, self.menu, self.sent = '321', False, []

        def command(self, op, **kw):
            self.sent.append((op, kw.get('action'), kw.get('value')))
            if op == 'act' and kw['action'] == 'open_accounts':
                self.menu = True
            elif op == 'act' and kw['action'] == 'choose_account':
                self.selected, self.menu = kw['value'], False
            if op != 'observe':
                return {'acted': True}
            task = kw.get('task') or {}
            controls = [dict(id=0, kind='account', enabled=True)]
            if self.menu:
                for i, digits in enumerate(('123', '321'), 1):
                    option = dict(id=i, kind='account_option', enabled=True)
                    if task.get('list_accounts'):
                        option.update(digits=digits, type='Individual' if digits == '123' else 'Contributory')
                    if task.get('account_digits'):
                        option['matches'] = digits == task['account_digits']
                    controls.append(option)
            match = None if not task.get('account_digits') else task['account_digits'] == self.selected
            return {'kind': 'history', 'token': 't', 'controls': controls, 'account_match': match}
    bridge = Bridge()
    collector = AccountCollector(bridge, None, store=tmp_path, sleep=lambda s: None)
    assert collector.listed() == [{'digits': '123', 'type': 'Individual'}, {'digits': '321', 'type': 'Contributory'}]
    collector.switch('123')
    assert bridge.selected == '123' and collector.account_digits == '123'
    assert ('act', 'choose_account', '123') in bridge.sent


LOTS = ('"Realized Gain/Loss - Lot Details for Fixture as of Tue Sep 29  21:55:09 EDT 2026 from 01/01/2025 to 12/31/2025",,,,\n'
        '"Symbol","Name","Closed Date","Opened Date","Quantity","Proceeds Per Share","Cost Per Share","Proceeds",'
        '"Cost Basis (CB)","Gain/Loss ($)","Gain/Loss (%)","Long Term Gain/Loss","Short Term Gain/Loss","Term",'
        '"Unadjusted Cost Basis","Wash Sale?","Disallowed Loss"\n'
        '"AAA","FIXTURE FUND","03/02/2025","01/05/2024","2.5","$120.00","$100.00","$300.00","$250.00","$50.00","20%",'
        '"$50.00","","Long Term","$250.00","No",""\n'
        '"BBB","FIXTURE CO","04/02/2025","03/01/2025","1","$80.00","$100.00","$80.00","$100.00","-$20.00","-20%",'
        '"","-$20.00","Short Term","$100.00","Yes","$15.00"\n')


def test_lots_csv():
    from pacioli.functions.schwab_export import parse_lots_csv
    parsed = parse_lots_csv(LOTS.encode())
    assert (parsed['window_start'], parsed['window_end']) == (date(2025, 1, 1), date(2025, 12, 31))
    first, second = parsed['rows']
    assert (first['symbol'], first['quantity'], first['opened_on'], first['closed_on'], first['proceeds'],
            first['cost_basis'], first['term'], first['disallowed_loss']) == (
        'AAA', D('2.5'), date(2024, 1, 5), date(2025, 3, 2), D('300.00'), D('250.00'), 'long', 0)
    assert second['term'] == 'short' and second['disallowed_loss'] == D('15.00')
    empty = parse_lots_csv(LOTS.splitlines()[0].encode() + b'\n"There are no transactions for this period."')
    assert empty['rows'] == []
