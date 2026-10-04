def test_login_page_loads(client):
    assert client.get('/login/').status_code == 200


def test_admin_models_require_login(client):
    response = client.get('/roles/', follow_redirects=False)

    assert response.status_code == 302
    assert '/login/' in response.headers['Location']


def test_logout_redirects(client, admin_user):
    client.post('/login/', data=admin_user)

    response = client.get('/logout/', follow_redirects=False)

    assert response.status_code == 302
    assert client.get('/roles/').status_code == 302
