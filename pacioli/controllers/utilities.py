

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