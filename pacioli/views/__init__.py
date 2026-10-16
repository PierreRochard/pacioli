from flask import url_for, redirect, request, abort, current_app
from flask_admin.contrib import sqla
from flask_admin.form import SecureForm
from flask_admin.model import typefmt
from flask_security import current_user
from flask_wtf.csrf import validate_csrf
from markupsafe import Markup
from wtforms.validators import ValidationError


def require_action_csrf():
    if not current_app.config.get('WTF_CSRF_ENABLED', True):
        return
    try:
        validate_csrf(request.form.get('csrf_token'))
    except ValidationError as error:
        abort(400, description=str(error))


def bool_icon(view, value, name):
    """Flask-Admin's boolean formatter uses Font Awesome; this theme ships Bootstrap Icons."""
    label = '{}: {}'.format(name, 'true' if value else 'false')
    return Markup('<i class="bi bi-{}" title="{}"></i>').format(
        'check-circle' if value else 'dash-circle', label)


class PrivateModelView(sqla.ModelView):
    form_base_class = SecureForm
    column_type_formatters = {**typefmt.BASE_FORMATTERS, bool: bool_icon}
    column_type_formatters_detail = {**typefmt.DETAIL_FORMATTERS, bool: bool_icon}

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
