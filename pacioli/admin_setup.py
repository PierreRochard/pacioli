"""One-use browser bootstrap for the first administrator."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_security.utils import encrypt_password
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError

from pacioli.extensions import db
from pacioli.models import AdminSetupLinks, Roles, Users


SETUP_LOCK = 72419035
LINK_LIFETIME = timedelta(hours=2)


def _lock_setup(session):
    # The issuer and browser POST serialize the zero-user check and token use.
    session.execute('SELECT pg_advisory_xact_lock({})'.format(SETUP_LOCK))


def issue_admin_link(session, public_url):
    """Return a link whose secret is kept only in the caller's output."""
    if not public_url.startswith('https://'):
        raise ValueError('Admin setup requires an HTTPS public URL')
    _lock_setup(session)
    if session.query(Users.id).first() is not None:
        raise ValueError('An account already exists; admin bootstrap is disabled')
    now = datetime.now(timezone.utc)
    session.query(AdminSetupLinks).filter(
        AdminSetupLinks.used_at.is_(None),
        AdminSetupLinks.revoked_at.is_(None)).update(
            {AdminSetupLinks.revoked_at: now}, synchronize_session=False)
    token = secrets.token_urlsafe(32)
    session.add(AdminSetupLinks(token_digest=sha256(token.encode('ascii')).hexdigest(),
                                created_at=now, expires_at=now + LINK_LIFETIME))
    session.flush()
    return public_url.rstrip('/') + '/setup/admin#' + token


def _private_response(response, nonce=None):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    if nonce:
        response.headers['Content-Security-Policy'] = (
            "default-src 'none'; style-src 'nonce-{0}'; "
            "script-src 'nonce-{0}'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'".format(nonce))
    return response


def _form_response(error=None, email='', token='', status=200):
    nonce = secrets.token_urlsafe(18)
    response = current_app.make_response((render_template(
        'admin_setup.html', error=error, email=email, token=token, nonce=nonce),
        status))
    return _private_response(response, nonce)


def _setup_admin():
    if request.method in ('GET', 'HEAD'):
        if db.session.query(Users.id).first() is not None:
            abort(404)
        return _form_response()

    if current_app.config.get('WTF_CSRF_ENABLED', True):
        try:
            validate_csrf(request.form.get('csrf_token'))
        except ValidationError:
            abort(400)

    token = request.form.get('token', '')
    if len(token) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
        abort(404)
    digest = sha256(token.encode('ascii')).hexdigest()
    _lock_setup(db.session)
    if db.session.query(Users.id).first() is not None:
        abort(404)
    link = (db.session.query(AdminSetupLinks)
            .filter_by(token_digest=digest)
            .with_for_update().one_or_none())
    now = datetime.now(timezone.utc)
    if (link is None or link.used_at is not None or link.revoked_at is not None
            or link.expires_at <= now):
        abort(404)

    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    confirmation = request.form.get('confirm_password', '')
    if len(email) > 254 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        db.session.rollback()
        return _form_response('Enter a valid email address.', email, token, 400)
    if not 16 <= len(password) <= 4096:
        db.session.rollback()
        return _form_response('Use a password of at least 16 characters.', email, token, 400)
    if password != confirmation:
        db.session.rollback()
        return _form_response('The passwords do not match.', email, token, 400)

    role = db.session.query(Roles).filter_by(name='administrator').first()
    if role is None:
        role = Roles(name='administrator', description='administrator')
        db.session.add(role)
    user = Users(email=email, password=encrypt_password(password),
                 active=True, confirmed_at=now.replace(tzinfo=None))
    user.roles.append(role)
    db.session.add(user)
    link.used_at = now
    db.session.commit()
    return _private_response(redirect(url_for('security.login')))


def register_admin_setup(app):
    app.add_url_rule('/setup/admin', 'setup_admin', _setup_admin,
                     methods=['GET', 'POST'])
