"""The Bootstrap 5 admin theme in a real headless Firefox, against the test database.

The app is served on loopback by a test-only server. Each page records its own
JavaScript errors, and the test fails on any of them.
"""

import re
import time
from threading import Thread
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from werkzeug.serving import make_server

from pacioli.extensions import db
from pacioli.functions import firefox_bidi as bidi
from pacioli.models import Connections, Mappings, Subaccounts
from tests.firefox_harness import launch_firefox


RECORD_ERRORS = '''() => {
    window.__themeErrors = [];
    window.addEventListener('error', event => window.__themeErrors.push(String(event.message)));
    window.addEventListener('unhandledrejection', event => window.__themeErrors.push(String(event.reason)));
}'''


@pytest.fixture
def site(app, admin_user):
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:{}'.format(server.server_port)
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def mapping(app_context):
    row = Mappings(source='ofx', keyword='theme-' + uuid4().hex[:8],
                   positive_debit_subaccount_id='Groceries', positive_credit_subaccount_id='Rent')
    db.session.add(row)
    db.session.commit()
    identifier = row.id
    yield row
    db.session.rollback()
    db.session.query(Mappings).filter_by(id=identifier).delete()
    db.session.commit()


@pytest.fixture
def connection_row(app_context):
    row = Connections(source='ofx_file', type='Checking', currency='USD', org='theme-' + uuid4().hex[:8])
    db.session.add(row)
    db.session.commit()
    identifier = row.id
    yield row
    db.session.rollback()
    db.session.query(Connections).filter_by(id=identifier).delete()
    db.session.commit()


def wait_for(check, what, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        assert time.monotonic() < deadline, 'Timed out waiting for ' + what
        time.sleep(.2)


def test_admin_theme_widgets_work_in_firefox(app, site, admin_user, mapping, connection_row,
                                             tmp_path, monkeypatch):
    elements_view = next(view for view in app.extensions['admin'][0]._views
                         if view.endpoint == 'elements')
    monkeypatch.setattr(elements_view, 'create_modal', True)
    with launch_firefox(tmp_path, 'about:blank') as firefox:
        with bidi.FirefoxConnection(firefox.port, allowed_origins={site}) as connection:
            connection.command('script.addPreloadScript', {
                'functionDeclaration': RECORD_ERRORS, 'contexts': [connection.tab]})
            page = connection.page
            # Background tabs throttle timers, which delays jQuery's ready handlers.
            page.goto(site + '/login/')
            page.bring_to_front()

            def run(body):
                return connection.script(page.context, body)

            def visit(path, ready):
                page.goto(site + path)
                wait_for(lambda: run('return document.readyState === "complete";'), path + ' to load')
                wait_for(lambda: run(ready), path + ' widgets')

            def no_errors():
                assert run('return window.__themeErrors;') == []

            page.locator('#email').fill(admin_user['email'])
            page.locator('#password').fill(admin_user['password'])
            page.locator('#submit').click()
            wait_for(lambda: urlsplit(page.url).path == '/', 'sign-in')

            # Navbar dropdowns use Bootstrap 5's own JavaScript.
            visit('/', 'return !!window.bootstrap;')
            page.locator('#admin-navbar-collapse a.dropdown-toggle').filter(has_text='Admin').click()
            wait_for(lambda: page.locator('.dropdown-menu.show').is_visible(), 'the Admin menu')
            no_errors()

            # Inline list editing saves through Flask-Admin's ajax_update view.
            visit('/mappings/', 'return !!document.querySelector("a.inline-editable");')
            edited = mapping.keyword + '-edited'
            page.locator('a[data-role="x-editable"][name="keyword"][data-pk="{}"]'.format(mapping.id)).click()
            page.locator('.inline-edit-form input').fill(edited)
            page.locator('.inline-edit-form button[type="submit"]').click()
            wait_for(lambda: run('return !document.querySelector(".inline-edit-form");'), 'the save')
            assert page.locator('a[name="keyword"][data-pk="{}"]'.format(mapping.id)).inner_text() == edited
            db.session.expire_all()
            assert db.session.get(Mappings, mapping.id).keyword == edited

            # Choice and datetime columns edit inline too; datetimes keep the server's format.
            visit('/connections/', 'return !!document.querySelector("a.inline-editable");')
            page.locator('a[name="currency"][data-pk="{}"]'.format(connection_row.id)).click()
            run('const s = document.querySelector(".inline-edit-form select"); s.value = "EUR";'
                ' s.dispatchEvent(new Event("change")); return null;')
            page.locator('.inline-edit-form button[type="submit"]').click()
            wait_for(lambda: run('return !document.querySelector(".inline-edit-form");'), 'the currency save')
            assert page.locator('a[name="currency"][data-pk="{}"]'.format(connection_row.id)).inner_text() == 'EUR'
            page.locator('a[name="synced_at"][data-pk="{}"]'.format(connection_row.id)).click()
            page.locator('.inline-edit-form input').fill('2026-10-16T12:34:56')
            page.locator('.inline-edit-form button[type="submit"]').click()
            wait_for(lambda: run('return !document.querySelector(".inline-edit-form");'), 'the datetime save')
            db.session.expire_all()
            saved = db.session.get(Connections, connection_row.id)
            assert saved.currency == 'EUR'
            assert saved.synced_at.strftime('%Y-%m-%d %H:%M:%S') == '2026-10-16 12:34:56'
            no_errors()

            # Filters build their operation menus with Select2 4.
            visit('/mappings/', 'return !!document.querySelector("a.inline-editable");')
            page.locator('a.dropdown-toggle').filter(has_text='Add Filter').click()
            page.locator('.field-filters a.filter').filter(has_text='Keyword').click()
            wait_for(lambda: run('return !!document.querySelector("table.filters .select2-container");'),
                     'a filter row')
            no_errors()

            # Select2 4 AJAX lookups fill Flask-Admin's hidden input with the primary key.
            visit('/mappings/new/', 'return document.querySelectorAll(".select2-container").length >= 6;')
            page.locator('#positive_debit_subaccount + select + .select2-container .select2-selection').click()
            page.locator('.select2-container--open .select2-search__field').fill('Groc')
            # Results read "Account - Subaccount".
            groceries = page.locator('.select2-results__option').filter(has_text='Groceries Expense - Groceries')
            wait_for(groceries.count, 'lookup results')
            groceries.click()
            groceries_id = db.session.query(Subaccounts.id).filter_by(name='Groceries').scalar()
            assert run('return document.getElementById("positive_debit_subaccount").value;') == str(groceries_id)
            no_errors()

            # daterangepicker 3 fills a date only when applied.
            visit('/paystubs/new/', 'return !!jQuery("input[data-role=datepicker]").first().data("daterangepicker");')
            field = run('return document.querySelector("input[data-role=datepicker]").id;')
            assert run('return document.getElementById("{}").value;'.format(field)) == ''
            page.locator('#' + field).click()
            wait_for(lambda: run('return [...document.querySelectorAll(".daterangepicker")]'
                                 '.some(el => getComputedStyle(el).display === "block");'), 'the date picker')
            run('[...document.querySelectorAll(".daterangepicker")].find(el => getComputedStyle(el)'
                '.display === "block").querySelector(".applyBtn").click(); return null;')
            assert re.fullmatch(r'\d{4}-\d{2}-\d{2}',
                                run('return document.getElementById("{}").value;'.format(field)))
            no_errors()

            # Remote modals load the create form and style its widgets.
            visit('/elements/', 'return !!document.querySelector("[data-bs-toggle=modal]");')
            page.locator('a[data-bs-toggle="modal"]').filter(has_text='Create').click()
            wait_for(lambda: run('return !!document.querySelector(".modal.show .modal-content form .select2-container");'),
                     'the modal form')
            # Click only once the dialog has finished sliding in.
            wait_for(lambda: run('return getComputedStyle(document.querySelector(".modal.show .modal-dialog"))'
                                 '.transform === "none";'), 'the modal animation')
            page.locator('.modal.show .btn-close').click()
            wait_for(lambda: run('return !document.querySelector(".modal.show");'), 'the modal to close')
            no_errors()
