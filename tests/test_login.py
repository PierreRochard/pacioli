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


# Flask-Security 3.0 made this pbkdf2_sha512 hash of 'legacy-password' with the
# test salt, as it did for accounts created before the Flask-Security 5 upgrade.
LEGACY_HASH = ('$pbkdf2-sha512$25000$mhMCQIhRijGm1LrXeg9hTA$CX62YXrn.bL.b4/0OgrOZtFUifR6Soj'
               'SUJbUG9YtesIM2g4H6A7b5G4KpAxkB7jsiE/0E5ww.3BJ7h0DND/MbA')


def test_legacy_password_hash_signs_in_and_is_upgraded(app, client):
    from datetime import datetime, timezone
    from uuid import uuid4

    from pacioli.extensions import db
    from pacioli.models import Roles, Users

    email = 'legacy-' + uuid4().hex + '@example.com'
    with app.app_context():
        role = Roles.query.filter_by(name='administrator').first() or Roles(name='administrator')
        user = Users(email=email, password=LEGACY_HASH, active=True,
                     confirmed_at=datetime.now(timezone.utc), roles=[role])
        db.session.add(user)
        db.session.commit()

    assert client.post('/login/', data={'email': email, 'password': 'wrong'}).status_code == 200
    response = client.post('/login/', data={'email': email, 'password': 'legacy-password'},
                           follow_redirects=False)
    assert response.status_code == 302
    assert client.get('/roles/').status_code == 200
    with app.app_context():
        assert Users.query.filter_by(email=email).one().password.startswith('$argon2')
