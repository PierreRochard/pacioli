

def name_for_scalar_relationship(base, local_cls, referred_cls, constraint):
    name = referred_cls.__name__.lower()
    local_table = local_cls.__table__
    if name in local_table.columns:
        newname = name + "_"
        return newname
    return name


def name_for_collection_relationship(base, local_cls, referred_cls, constraint):
    name = referred_cls.__name__.lower() + '_collection'
    for c in referred_cls.__table__.columns:
        if c == name:
            name += "_"
    return name


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