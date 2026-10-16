"""Every admin page renders with the Bootstrap 5 theme and loads only vendored assets."""

import re


# Markup or assets that only Flask-Admin's Bootstrap 4 theme would produce.
BOOTSTRAP4 = re.compile(
    r'data-(toggle|target|dismiss)=|class="[^"]*\b(form-group|form-inline|custom-select|'
    r'input-group-(append|prepend)|float-(left|right)|sr-only)\b|glyphicon|\bfa fa-|'
    r'bootstrap4|font-awesome|vendor/x-editable/|select2/select2\.css')
# Bootstrap-agnostic Flask-Admin scripts the theme still uses unchanged.
FLASK_ADMIN_SCRIPTS = {'/static/admin/admin/js/helpers.js', '/static/admin/admin/js/actions.js',
                       '/static/admin/admin/js/details_filter.js'}
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
        assert '/static/vendor/bootstrap/bootstrap.min.css' in html, page
        rendered += 1
        assets.update(re.findall(r'(?:src|href)="(/static/[^"?]+)', html))
    assert rendered > 40
    assert {'/static/theme/form.js', '/static/theme/list.js',
            '/static/vendor/select2/select2.min.js'} <= assets
    assert {asset for asset in assets if asset.startswith('/static/admin/')} <= FLASK_ADMIN_SCRIPTS
    for asset in sorted(assets):
        assert client.get(asset).status_code == 200, asset


# The theme ports these Flask-Admin 2.2.1 files to Bootstrap 5, and its scripts
# read the markup of these widgets and run these scripts unchanged. If an
# upgrade changes any of them, review pacioli/templates/admin and
# pacioli/static/theme against the new version, then update the digests.
UPSTREAM = {
    'templates/bootstrap4/admin/base.html': 'a6add6a45c2ac3c5',
    'templates/bootstrap4/admin/layout.html': '5e62de8b5e527fc7',
    'templates/bootstrap4/admin/lib.html': '21a2c36fee3e1984',
    'templates/bootstrap4/admin/actions.html': '685288ebd9108462',
    'templates/bootstrap4/admin/model/layout.html': '9801f7f6e21b6dfe',
    'templates/bootstrap4/admin/model/list.html': '21003d92feb9a75c',
    'templates/bootstrap4/admin/model/create.html': '8930a13e4d81cd59',
    'templates/bootstrap4/admin/model/edit.html': 'b606efe0a695ead2',
    'templates/bootstrap4/admin/model/details.html': 'c4aa5d6ca9b9b82d',
    'templates/bootstrap4/admin/model/row_actions.html': '4dc1ade152ecd029',
    'templates/bootstrap4/admin/model/inline_list_base.html': '9385d36000f838cd',
    'templates/bootstrap4/admin/model/modals/create.html': 'cee8f2d9804f4c08',
    'templates/bootstrap4/admin/model/modals/edit.html': 'df182d3d8904c068',
    'templates/bootstrap4/admin/model/modals/details.html': '6b25f1078b54b19d',
    'static/admin/js/form.js': '1197b5648ef137a8',
    'static/admin/js/bs4_filters.js': '96b81607e075be9d',
    'static/admin/js/bs4_modal.js': 'aba57d8580d8427b',
    'static/admin/css/bootstrap4/admin.css': '3df00acfe73dfbc5',
    'model/widgets.py': '25fd3e61b0e83dab',
    'form/widgets.py': 'b608ffb1ae79661b',
    'static/admin/js/helpers.js': '948ac8b7cee0058a',
    'static/admin/js/actions.js': '16e9981042f7fcca',
    'static/admin/js/details_filter.js': '5ce956d94e548837',
}


def test_flask_admin_files_behind_the_theme_are_unchanged():
    import hashlib
    from pathlib import Path
    import flask_admin
    root = Path(flask_admin.__file__).parent
    changed = [name for name, digest in UPSTREAM.items()
               if hashlib.sha256((root / name).read_bytes()).hexdigest()[:16] != digest]
    assert changed == [], 'Flask-Admin changed files the Bootstrap 5 theme was ported from'
