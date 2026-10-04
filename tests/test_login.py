def test_administrator_can_log_in(client, admin_user):
    response = client.post('/login/', data={
        'email': admin_user['email'],
        'password': admin_user['password'],
    }, follow_redirects=False)

    assert response.status_code == 302
    assert client.get('/roles/').status_code == 200


def test_wrong_password_does_not_log_in(client, admin_user):
    response = client.post('/login/', data={
        'email': admin_user['email'],
        'password': 'incorrect',
    }, follow_redirects=False)

    assert response.status_code == 200
    assert client.get('/roles/').status_code == 302
