

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