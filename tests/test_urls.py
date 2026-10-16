def test_login_page_loads(client):
    assert client.get('/login/').status_code == 200


def test_admin_models_require_login(client):
    response = client.get('/roles/', follow_redirects=False)

    assert response.status_code == 302
    assert '/login/' in response.headers['Location']


def test_every_signed_in_page_loads(app, client, admin_user):
    # Auth pages, logout and the never-ending event stream are not ordinary pages.
    skipped = ('/login/', '/logout/', '/confirm/', '/reset/', '/setup/admin',
               '/connections/chase/local/events/')
    pages = sorted(rule.rule for rule in app.url_map.iter_rules()
                   if 'GET' in rule.methods and not rule.arguments
                   and not rule.rule.startswith('/static')
                   and not rule.rule.endswith(('/new/', '/edit/', '/details/', '/ajax/lookup/'))
                   and rule.rule not in skipped)
    client.post('/login/', data=admin_user)
    failures = []
    for page in pages:
        try:
            status = client.get(page).status_code
        except Exception as error:
            status = type(error).__name__
        if status != 200:
            failures.append((page, status))
    assert len(pages) > 40 and failures == []


def test_logout_redirects(client, admin_user):
    client.post('/login/', data=admin_user)

    response = client.post('/logout/', follow_redirects=False)

    assert response.status_code == 302
    assert client.get('/roles/').status_code == 302
