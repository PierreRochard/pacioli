from pacioli import admin, db
from pacioli.models import TaxTags
from pacioli.views import PrivateModelView


class TaxModelView(PrivateModelView):
    can_create = True
    can_delete = True
    can_edit = True
    can_export = True


class TaxTagsModelView(TaxModelView):
    pass


admin.add_view(TaxTagsModelView(TaxTags, db.session,
                                     name='Tags', category='Tax', endpoint='tax/tags'))
