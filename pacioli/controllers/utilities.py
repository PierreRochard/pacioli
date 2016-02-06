from datetime import datetime
from decimal import Decimal


def account_formatter(view, context, model, name):
    # `view` is current administrative view
    # `context` is instance of jinja2.runtime.Context
    # `model` is model instance
    # `name` is property name
    acct_to_account = dict(bankacctfrom='Bank Account',
                           ccacctfrom='Credit Card')
    return acct_to_account[getattr(model, name)]


def currency_formatter(view, context, model, name):
    return "{0:,.2f}".format(getattr(model, name))


def date_formatter(view, context, model, name):
    return getattr(model, name).strftime('%Y-%m-%d')


def id_formatter(view, context, model, name):
    return '...' + str(getattr(model, name))[-4:-1]


def type_formatter(view, context, model, name):
    return getattr(model, name).lower().title()


def results_to_table(query_results):
    html_body = '<table style="border:1px solid black;"><thead><tr>'
    for header in ['ID', 'Date', 'Debit', 'Credit', 'Description']:
        html_body += '<th style="border:1px solid black;">{0}</th>'.format(header)
    html_body += '</tr></thead><tbody>'
    for row in query_results:
        html_body += '<tr>'
        for cell in row:
            if isinstance(cell, Decimal):
                if cell > 0:
                    html_body += '<td style="border:1px solid black;">{0:,.2f}</td><td style="border:1px solid black;"></td>'.format(cell)
                else:
                    html_body += '<td style="border:1px solid black;"></td><td style="border:1px solid black;">{0:,.2f}</td>'.format(cell)
            elif isinstance(cell, datetime):
                cell = cell.date()
                html_body += '<td style="border:1px solid black;">{0}</td>'.format(cell)
            else:
                html_body += '<td style="border:1px solid black;">{0}</td>'.format(cell)
        html_body += '</tr>'
    html_body += '</tbody></table>'
    return html_body


