import string
from datetime import datetime, date

from flask import url_for
from markupsafe import Markup


def account_formatter(view, context, model, name):
    acct_to_account = dict(bankacctfrom='Bank Account',
                           ccacctfrom='Credit Card',
                           invacctfrom='Investments')
    return acct_to_account[getattr(model, name)]


def string_formatter(view, context, model, name):
    column_string = getattr(model, name)
    column_string = column_string.lower()
    column_string = string.capwords(column_string, ' ')
    return column_string


def currency_formatter(view, context, model, name):
    if getattr(model, name):
        currency_string = "{0:,.2f}".format(getattr(model, name))
        if currency_string.startswith('-'):
            currency_string = currency_string.replace('-', '(')
            currency_string += ')'
        return currency_string
    else:
        return '-'


def percent_formatter(view, context, model, name):
    if getattr(model, name):
        currency_string = "{0:,.2f}%".format(getattr(model, name)*100)
        if currency_string.startswith('-'):
            currency_string = currency_string.replace('-', '(')
            currency_string += ')'
        return currency_string
    else:
        return '-'


def fs_currency_format(amount):
    currency_string = "{0:,.2f}".format(amount)
    if currency_string.startswith('-'):
        currency_string = currency_string.replace('-', '(')
        currency_string += ')'
    return currency_string


def fs_linked_currency_formatter(view, context, model, name):
    if getattr(model, name):

        if view.endpoint == 'balance-sheet':
            amount = getattr(model, name)
            return Markup('<a href={0}>{1}</a>'.format(url_for('journalentries.index_view', subaccount=model.subaccount,
                                                               period_interval=view._template_args['period_interval'],
                                                               period=view._template_args['period'],
                                                               cumulative='balance-sheet'),
                                                       fs_currency_format(amount)))
        else:
            amount = -getattr(model, name)
            return Markup('<a href={0}>{1}</a>'.format(url_for('journalentries.index_view', subaccount=model.subaccount,
                                                               period_interval=view._template_args['period_interval'],
                                                               period=view._template_args['period']),
                                                       fs_currency_format(amount)))
    else:
        return '-'


def date_formatter(view, context, model, name):
    date_object = getattr(model, name)
    if isinstance(date_object, datetime) or isinstance(date_object, date):
        return date_object.strftime('%Y-%m-%d')
    elif not date_object:
        return ''
    else:
        return str(date_object)


def id_formatter(view, context, model, name):
    return '...' + str(getattr(model, name))[-4:-1]


def link_mapping_formatter(view, context, model, name):
    link = Markup('''
    <a target="_blank" href="{1}">
         {0}
    </a>
    '''.format(getattr(model, name), url_for('mappings.index_view', flt1_0=getattr(model, name)), getattr(model, name)))
    return link


def link_transaction_search_formatter(view, context, model, name):
    link = Markup('''
    <form class="icon" method="POST" action="/mappings/delete/">
      <input id="id" name="id" type="hidden" value="{2}">
      <input id="url" name="url" type="hidden" value="{3}">

      <button onclick="return confirm('Are you sure you want to delete this record?');" title="Delete record">
        <span class="fa fa-trash glyphicon glyphicon-trash"></span>
      </button>
    </form>
        <a target="_blank" href="{1}">
             {0}
        </a>
        '''.format(getattr(model, name), url_for('banking/transactions.index_view', search=getattr(model, name)),
                   getattr(model, 'mapping_id_' + name[-1]), view.url))
    return link
