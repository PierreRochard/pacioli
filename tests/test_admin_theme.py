"""Every admin page renders with the Bootstrap 5 theme of Pacioli's Flask-Admin build."""

from importlib.metadata import version
import re


# Markup or assets that only Flask-Admin's Bootstrap 4 theme would produce.
BOOTSTRAP4 = re.compile(
    r'data-(toggle|target|dismiss)=|class="[^"]*\b(form-group|form-inline|custom-select|'
    r'input-group-(append|prepend)|float-(left|right)|sr-only)\b|glyphicon|\bfa fa-|'
    r'bootstrap4|font-awesome|vendor/x-editable/|select2/select2\.css')
# Files the Bootstrap 4 theme served, which the patched build no longer ships.
BOOTSTRAP4_FILES = ('/static/admin/vendor/jquery.min.js', '/static/admin/vendor/select2/select2.css',
                    '/static/admin/vendor/x-editable/js/bootstrap4-editable.min.js',
                    '/static/admin/bootstrap/bootstrap4/css/bootstrap.min.css')
SKIPPED = ('/login/', '/logout/', '/confirm/', '/reset/', '/setup/admin',
           '/connections/chase/local/events/')


def admin_pages(app):
    return sorted(rule.rule for rule in app.url_map.iter_rules()
                  if 'GET' in rule.methods and not rule.arguments
                  and not rule.rule.startswith(('/static', '/fs-static'))
                  and not rule.rule.endswith(('/edit/', '/details/', '/ajax/lookup/'))
                  and rule.rule not in SKIPPED)


def test_admin_pages_use_the_bootstrap5_theme(app, client, admin_user):
    client.post('/login/', data=admin_user)
    rendered, assets = 0, set()
    for page in admin_pages(app):
        response = client.get(page)
        if response.status_code != 200 or response.mimetype != 'text/html':
            continue
        html = response.get_data(as_text=True)
        found = BOOTSTRAP4.search(html)
        assert found is None, (page, found and found.group(0))
        assert '/static/admin/vendor/bootstrap/bootstrap.min.css' in html, page
        rendered += 1
        assets.update(re.findall(r'(?:src|href)="(/static/[^"?]+)', html))
    assert rendered > 40
    assert {'/static/admin/admin/js/form.js', '/static/admin/admin/js/bs5_filters.js',
            '/static/admin/vendor/select2/select2.min.js'} <= assets
    for asset in sorted(assets):
        assert client.get(asset).status_code == 200, asset


def test_flask_admin_is_the_patched_build(client):
    # requirements.in pins a wheel built from the private flask-admin-private repository.
    assert '+pacioli.' in version('flask-admin')
    for path in BOOTSTRAP4_FILES:
        assert client.get(path).status_code == 404, path
