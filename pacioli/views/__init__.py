from flask import url_for, redirect, request, abort
from flask.ext.admin.contrib import sqla
from flask_security import current_user


def redirect_url(default='index'):
    return request.args.get('next') or request.referrer or url_for(default)


class PacioliModelView(sqla.ModelView):
    def is_accessible(self):
        if not current_user.is_active or not current_user.is_authenticated:
            return False

        if current_user.has_role('administrator'):
            return True

        return False

    def _handle_view(self, name, **kwargs):
        if not self.is_accessible():
            if current_user.is_authenticated:
                abort(403)
            else:
                return redirect(url_for('security.login', next=request.url))

    can_view_details = True
    column_display_pk = True
    column_display_all_relations = False
