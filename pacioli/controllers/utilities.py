from datetime import datetime, date
from decimal import Decimal
import os

from jinja2 import Template
from premailer import transform


def account_formatter(view, context, model, name):
    # `view` is current administrative view
    # `context` is instance of jinja2.runtime.Context
    # `model` is model instance
    # `name` is property name
    acct_to_account = dict(bankacctfrom='Bank Account',
                           ccacctfrom='Credit Card')
    return acct_to_account[getattr(model, name)]


def currency_formatter(view, context, model, name):
    if getattr(model, name):
        currency_string = "{0:,.2f}".format(getattr(model, name))
        if currency_string.startswith('-'):
            currency_string = currency_string.replace('-', '(')
            currency_string += ')'
        return currency_string
    else:
        return '-'


def income_statement_currency_formatter(view, context, model, name):
    if getattr(model, name):
        amount = -getattr(model, name)
        currency_string = "{0:,.2f}".format(amount)
        if currency_string.startswith('-'):
            currency_string = currency_string.replace('-', '(')
            currency_string += ')'
        return currency_string
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


def type_formatter(view, context, model, name):
    return getattr(model, name).lower().title()


def results_to_email_template(title, table_caption, table_header, query_results):
    templates_directory = os.path.abspath(__file__ + "/../../templates")

    email_template = os.path.join(templates_directory, 'email_table_template.html')
    with open(email_template, 'r') as html_template:
        html_template_string = html_template.read()

    css_template = os.path.join(templates_directory, 'email_bootstrap.min.css')
    with open(css_template, 'r') as css:
        css_string = css.read()

    template = Template(html_template_string)

    html_body = template.render(title=title,
                                css=css_string,
                                table_caption=table_caption,
                                table_header=table_header,
                                table_rows=query_results).encode('utf-8')

    return transform(html_body).encode('utf-8')
