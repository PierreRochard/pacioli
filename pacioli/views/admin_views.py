from datetime import datetime, timezone
import json

from dateutil.tz import tzlocal
from flask import abort, current_app, flash, jsonify, make_response, redirect, request, url_for, Response, stream_with_context
from flask_admin import expose
from flask_security import current_user
from pacioli.extensions import admin
from pacioli.functions.ofx_functions import sync_ofx
from pacioli.models import (db, Users, Roles, Connections, Mappings, ConnectionResponses,
                            MappingOverlaps, ChaseBrowserCredentials, ChaseBrowserRuns)
from pacioli.views import PrivateModelView, require_action_csrf
from pacioli.views.utilities import date_formatter, link_mapping_formatter, link_transaction_search_formatter
from wtforms import PasswordField


class KeepBlankPasswordField(PasswordField):
    def populate_obj(self, obj, name):
        if self.data:
            super(KeepBlankPasswordField, self).populate_obj(obj, name)


class UserModelView(PrivateModelView):
    column_list = ('id', 'email', 'active', 'confirmed_at', 'current_login_at',
                   'last_login_ip', 'current_login_ip', 'login_count')
    column_formatters = dict(confirmed_at=date_formatter, current_login_at=date_formatter)


admin.add_view(UserModelView(Users, db.session, category='Admin'))
admin.add_view(PrivateModelView(Roles, db.session, category='Admin'))


class ConnectionsModelView(PrivateModelView):
    list_template = 'connections.html'
    column_list = ('id', 'source', 'type', 'currency', 'url', 'org', 'fid', 'routing_number',
                   'account_number', 'user', 'synced_at')
    form_choices = dict(type=[('Checking', 'Checking'), ('Savings', 'Savings'),
                              ('Credit Card', 'Credit Card'), ('File Import', 'File Import'),
                              ('Browser QFX', 'Browser QFX')],
                        source=[('ofx', 'ofx'), ('ofx_file', 'OFX/QFX file'),
                                ('chase_browser', 'Chase browser'),
                                ('amazon', 'amazon'), ('gmail', 'gmail')],
                        currency=[('USD', 'USD'), ('EUR', 'EUR'), ('BTC', 'BTC')])
    column_editable_list = column_list[1:]
    column_labels = dict(id='ID', url='URL', fid='FID')
    column_formatters = dict(synced_at=date_formatter)
    column_details_exclude_list = ('password',)
    form_overrides = dict(password=KeepBlankPasswordField)

    def after_model_change(self, form, model, is_created):
        if is_created:
            model.created_at = datetime.now(tzlocal())
            db.session.commit()

    @expose('/schwab/', methods=('GET', 'POST'))
    def schwab_connection(self):
        """Save the Schwab sign-in encrypted; it is never displayed again."""
        from pacioli.models import BrokerageCredentials
        credentials = db.session.query(BrokerageCredentials).get('schwab')
        if request.method == 'POST':
            require_action_csrf()
            action = request.form.get('action')
            if action == 'save':
                username = request.form.get('username', '').strip()
                password = request.form.get('password', '')
                if (not username or not password or len(username) > 254 or len(password) > 512 or
                        any(ord(char) < 32 for char in username + password)):
                    flash('Enter both the Schwab login ID and password.', 'error')
                else:
                    if credentials is None:
                        credentials = BrokerageCredentials(provider='schwab')
                        db.session.add(credentials)
                    credentials.username, credentials.password = username, password
                    credentials.updated_at = datetime.now(timezone.utc)
                    db.session.commit()
                    flash('Schwab login saved encrypted in Pacioli.', 'success')
            elif action == 'forget' and credentials is not None:
                db.session.delete(credentials)
                db.session.commit()
                flash('Schwab login removed from Pacioli.', 'success')
            return redirect(url_for('.schwab_connection'))
        response = make_response(self.render('schwab_connection.html', configured=credentials is not None,
                                             updated_at=credentials.updated_at if credentials else None))
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @expose('/sync_connections/', methods=('POST',))
    def sync_connections(self):
        require_action_csrf()
        sync_ofx()
        return redirect(url_for('connections.index_view'))

    @expose('/import_chase/', methods=('GET', 'POST'))
    def import_chase(self):
        """Receive a manually exported Chase QFX file; never ask for a login."""
        from pacioli.functions.ofx_file_import import (MAX_OFX_BYTES,
                                                        chase_account_summaries,
                                                        import_chase_qfx)
        result, error = None, None
        if request.method == 'POST':
            require_action_csrf()
            uploaded = request.files.get('statement')
            if uploaded is None or not uploaded.filename.lower().endswith(('.qfx', '.ofx')):
                error = 'Choose a Chase .qfx or .ofx file.'
            else:
                content = uploaded.stream.read(MAX_OFX_BYTES + 1)
                try:
                    result = import_chase_qfx(
                        content, reviewed_usd=request.form.get('reviewed_usd') == 'yes')
                except ValueError as exc:
                    error = str(exc)
                except Exception as exc:
                    current_app.logger.warning('Chase QFX import failed: %s',
                                               type(exc).__name__)
                    error = 'Could not import this file. Check that it is a Chase QFX bank or card statement.'
        return self.render('chase_qfx_import.html', result=result, error=error,
                           summaries=chase_account_summaries())

    @expose('/chase/local/', methods=('GET', 'POST'))
    def chase_local(self):
        from datetime import date, timedelta
        from pathlib import Path
        from pacioli.functions import chase_local
        from pacioli.functions.chase_local_bridge import LocalChaseError
        from pacioli.functions.chase_browser import cancel_run
        from pacioli.functions.ofx_file_import import chase_account_summaries, public_account_summary
        if request.method == 'POST':
            require_action_csrf()
            cursor_args = self._local_cursor() if request.accept_mimetypes.best == 'application/json' else (0, 0)
            try:
                action = request.form.get('action')
                if action == 'update':
                    chase_local.update(current_user.id)
                elif action == 'preferences':
                    chase_local.save_preferences(current_user.id, request.form.getlist('account_id'),
                                                 request.form.get('overlap_days'))
                    flash('Chase update settings saved.', 'success')
                elif action in ('start', 'preview'):
                    chase_local.start(current_user.id, request.form.getlist('account_id'),
                        request.form.get('start', ''), request.form.get('end', ''),
                        request.form.get('reviewed_usd') == 'yes', preview=action == 'preview')
                elif action in ('resume', 'cancel'):
                    run = db.session.query(ChaseBrowserRuns).get(int(request.form.get('run_id', '0')))
                    if run is None or run.mode not in chase_local.MODES or run.requested_by_id != current_user.id:
                        abort(404)
                    if action == 'resume': chase_local.resume(run.id, current_user.id)
                    else: cancel_run(run.id)
                else:
                    abort(400)
            except (LocalChaseError, ValueError) as error:
                db.session.rollback()
                code = getattr(error, 'code', 'invalid_export_request')
                message = chase_local.MESSAGES.get(code, 'Could not start this request: ' + code)
                if request.accept_mimetypes.best == 'application/json':
                    response = jsonify({'error': message})
                    response.headers['Cache-Control'] = 'no-store'
                    return response, 400
                flash(message, 'error')
            if request.accept_mimetypes.best == 'application/json':
                from pacioli.functions.chase_local_events import snapshot
                response = jsonify(snapshot(current_user.id, *cursor_args))
                response.headers['Cache-Control'] = 'no-store'
                return response
            return redirect(url_for('connections.chase_local'))
        response = make_response(self.render('chase_local.html',
            balances=[public_account_summary(item) for item in chase_account_summaries()],
            accounts=chase_local.accounts(), run=chase_local.public_status(chase_local.latest(current_user.id)),
            preferences=chase_local.preferences(current_user.id),
            extension_manifest=str(Path(__file__).resolve().parents[2] / 'extensions/chase-local/manifest.json'),
            today=date.today().isoformat(), start=(date.today()-timedelta(days=30)).isoformat()))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @expose('/chase/local/status/', methods=('GET',))
    def chase_local_status(self):
        from pacioli.functions.chase_local_events import snapshot
        run_id, cursor = self._local_cursor()
        response = jsonify(snapshot(current_user.id, run_id, cursor))
        response.headers['Cache-Control'] = 'no-store'
        return response

    def _local_cursor(self):
        try:
            saved = request.headers.get('Last-Event-ID')
            values = saved.split(':') if saved else (request.args.get('run_id', '0'), request.args.get('after', '0'))
            run_id, cursor = [int(value) for value in values]
            if not (0 <= run_id < 2**31 and 0 <= cursor < 2**63):
                raise ValueError()
            return run_id, cursor
        except (ValueError, TypeError):
            abort(400)

    @expose('/chase/local/events/', methods=('GET',))
    def chase_local_events(self):
        from pacioli.functions.chase_local_events import STREAM_SLOTS, stream
        run_id, cursor = self._local_cursor()
        user_id = current_user.id
        if not STREAM_SLOTS.acquire(blocking=False):
            return Response(status=503, headers={'Retry-After': '2', 'Cache-Control': 'no-store'})
        try:
            response = Response(stream_with_context(stream(user_id, run_id, cursor)),
                                mimetype='text/event-stream')
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Accel-Buffering'] = 'no'
            response.call_on_close(STREAM_SLOTS.release)
            return response
        except BaseException:
            STREAM_SLOTS.release()
            raise

    @expose('/chase/', methods=('GET', 'POST'))
    def chase_browser(self):
        """Configure and supervise a Chase browser export on this workstation."""
        from pacioli.functions.chase_browser import (
            ACTIVE_STATUSES, ChaseBrowserError, cancel_run,
            confirm_passkey_enrollment, confirm_provider_signin,
            latest_run, reject_passkey_enrollment, select_accounts,
            request_browser, reset_browser_profile, start_run, submit_mfa_code)

        connection = (db.session.query(Connections)
                      .filter_by(source='chase_browser', org='Chase').first())
        credentials = ((db.session.query(ChaseBrowserCredentials)
                        .filter_by(connection_id=connection.id).first())
                       if connection is not None else None)
        if request.method == 'POST':
            require_action_csrf()
            action = request.form.get('action')
            try:
                if action == 'save':
                    if connection is not None:
                        active = latest_run(connection.id)
                        if active is not None and active.status in ACTIVE_STATUSES:
                            raise ChaseBrowserError('another_run_active')
                    username = request.form.get('username', '').strip()
                    password = request.form.get('password', '')
                    if len(username) > 254 or len(password) > 512 or any(
                            ord(char) < 32 for char in username + password):
                        raise ChaseBrowserError('invalid_credentials')
                    if credentials is None and (not username or not password):
                        raise ChaseBrowserError('credentials_required')
                    if connection is None:
                        connection = Connections(
                            source='chase_browser', org='Chase', type='Browser QFX',
                            currency='USD', url='https://secure.chase.com/',
                            created_at=datetime.now(timezone.utc))
                        db.session.add(connection)
                        db.session.flush()
                    if credentials is None:
                        credentials = ChaseBrowserCredentials(
                            connection_id=connection.id, username=username,
                            password=password, browser_engine=current_app.config.get(
                                'CHASE_BROWSER_DEFAULT', 'firefox'))
                        db.session.add(credentials)
                    else:
                        if ((username and username != credentials.username) or password):
                            reset_browser_profile(credentials)
                        if username:
                            if username != credentials.username:
                                credentials.session_state = None
                                credentials.selected_account_labels = None
                                credentials.selected_ofx_account_ids = None
                                credentials.passkey_credential = None
                                credentials.passkey_verified_at = None
                            credentials.username = username
                        if password:
                            credentials.session_state = None
                            credentials.password = password
                        credentials.updated_at = datetime.now(timezone.utc)
                    db.session.commit()
                    flash('Chase login saved encrypted in Pacioli.', 'success')
                elif action == 'browser_engine':
                    if credentials is None:
                        raise ChaseBrowserError('credentials_missing')
                    active = latest_run(connection.id)
                    if active is not None and active.status in ACTIVE_STATUSES:
                        raise ChaseBrowserError('another_run_active')
                    engine = request.form.get('browser_engine')
                    if engine not in ('firefox', 'chromium'):
                        abort(400)
                    if engine != credentials.browser_engine:
                        credentials.browser_engine = engine
                        credentials.provider_verified_at = None
                        db.session.commit()
                    flash('Chase browser selection saved.', 'success')
                elif action == 'test_firefox':
                    if credentials is None:
                        raise ChaseBrowserError('credentials_missing')
                    start_run(connection.id, current_user.id, reviewed_usd=False,
                              mode='firefox_test')
                    flash('Checking Chase in your Firefox. No transactions will be downloaded.', 'success')
                elif action == 'start':
                    if connection is None:
                        raise ChaseBrowserError('credentials_missing')
                    start_run(connection.id, current_user.id,
                              reviewed_usd=request.form.get('reviewed_usd') == 'yes')
                    flash('Chase browser started on this workstation.', 'success')
                elif action == 'sync_now':
                    if connection is None:
                        raise ChaseBrowserError('credentials_missing')
                    start_run(connection.id, current_user.id,
                              reviewed_usd=True, mode='automatic')
                    flash('On-demand Chase sync started.', 'success')
                elif action == 'enroll_passkey':
                    if connection is None:
                        raise ChaseBrowserError('credentials_missing')
                    start_run(connection.id, current_user.id, reviewed_usd=False,
                              mode='passkey_enroll')
                    flash('A visible Chase passkey enrollment session started.', 'success')
                elif action == 'test_passkey':
                    if connection is None:
                        raise ChaseBrowserError('credentials_missing')
                    start_run(connection.id, current_user.id, reviewed_usd=False,
                              mode='passkey_test')
                    flash('Testing the local passkey in a fresh Chase browser.', 'success')
                elif action in ('test_provider', 'test_lastpass'):
                    if connection is None:
                        raise ChaseBrowserError('credentials_missing')
                    start_run(connection.id, current_user.id, reviewed_usd=False,
                              mode='lastpass_test' if action == 'test_lastpass' else 'provider_test')
                    flash('Use your existing passkey in the visible Chase window, then confirm here after sign-in.', 'success')
                elif action == 'forget_passkey':
                    if credentials is None:
                        raise ChaseBrowserError('credentials_missing')
                    active = latest_run(connection.id)
                    if active is not None and active.status in ACTIVE_STATUSES:
                        raise ChaseBrowserError('another_run_active')
                    credentials.passkey_credential = None
                    credentials.passkey_verified_at = None
                    credentials.updated_at = datetime.now(timezone.utc)
                    db.session.commit()
                    flash('The local passkey copy was removed. Remove it from Chase separately if needed.', 'success')
                elif action in ('select', 'cancel', 'mfa_code',
                                'confirm_passkey', 'reject_passkey',
                                'open_browser', 'confirm_provider'):
                    run_id = request.form.get('run_id', '')
                    if not run_id.isdigit() or connection is None:
                        abort(400)
                    run = db.session.query(ChaseBrowserRuns).get(int(run_id))
                    if run is None or run.connection_id != connection.id:
                        abort(404)
                    if action == 'select':
                        select_accounts(run.id, request.form.getlist('account_index'))
                    elif action == 'mfa_code':
                        submit_mfa_code(run.id, request.form.get('code'))
                        flash('One-time code sent to the local Chase browser.', 'success')
                    elif action == 'confirm_passkey':
                        confirm_passkey_enrollment(run.id)
                        flash('Saving the passkey after Chase confirmation.', 'success')
                    elif action == 'reject_passkey':
                        reject_passkey_enrollment(run.id)
                        flash('Chase rejection recorded. The browser is stopping without saving a key.', 'info')
                    elif action == 'open_browser':
                        request_browser(run.id)
                        flash('Opening the verification window on this workstation.', 'success')
                    elif action == 'confirm_provider':
                        confirm_provider_signin(run.id)
                    else:
                        cancel_run(run.id)
                elif action == 'forget':
                    if credentials is not None:
                        run = latest_run(connection.id)
                        if run is not None and run.status in ACTIVE_STATUSES:
                            raise ChaseBrowserError('another_run_active')
                        reset_browser_profile(credentials)
                        db.session.delete(credentials)
                        db.session.commit()
                    flash('Saved Chase login removed.', 'success')
                else:
                    abort(400)
            except ChaseBrowserError as error:
                db.session.rollback()
                flash(_chase_message(error.code), 'error')
            return redirect(url_for('connections.chase_browser'))

        run = latest_run(connection.id) if connection is not None else None
        engine = credentials.browser_engine if credentials else current_app.config.get(
            'CHASE_BROWSER_DEFAULT', 'firefox')
        response = make_response(self.render(
            'chase_connection.html', configured=credentials is not None,
            browser_engine=engine,
            passkey_enrollment_enabled=current_app.config.get(
                'CHASE_PASSKEY_ENROLLMENT_ENABLED', False),
            passkey_saved=bool(credentials and credentials.passkey_credential),
            passkey_verified=bool(credentials and credentials.passkey_verified_at),
            provider_verified=bool(credentials and credentials.provider_verified_at),
            saved_account_count=len(credentials.selected_ofx_account_ids or [])
                                if credentials else 0,
            run=run, active_statuses=ACTIVE_STATUSES,
            run_message=_chase_status_message(run, engine) if run else None))
        response.headers['Cache-Control'] = 'no-store'
        return response

    @expose('/chase/status/', methods=('GET',))
    def chase_browser_status(self):
        from pacioli.functions.chase_browser import latest_run

        connection = (db.session.query(Connections)
                      .filter_by(source='chase_browser', org='Chase').first())
        run = latest_run(connection.id) if connection is not None else None
        credentials = ((db.session.query(ChaseBrowserCredentials)
                        .filter_by(connection_id=connection.id).first())
                       if connection is not None else None)
        payload = {'configured': credentials is not None,
                   'browser_engine': credentials.browser_engine if credentials else
                                     current_app.config.get('CHASE_BROWSER_DEFAULT', 'firefox'),
                   'passkey_enrollment_enabled': current_app.config.get(
                       'CHASE_PASSKEY_ENROLLMENT_ENABLED', False),
                   'passkey_saved': bool(credentials and credentials.passkey_credential),
                   'passkey_verified': bool(credentials and credentials.passkey_verified_at),
                   'provider_verified': bool(credentials and credentials.provider_verified_at),
                   'saved_account_count': len(credentials.selected_ofx_account_ids or [])
                                          if credentials else 0}
        if run is not None:
            payload['run'] = {
                'id': run.id,
                'status': run.status,
                'mode': run.mode,
                'mfa_prompt': run.mfa_prompt,
                'browser_visible': run.browser_visible,
                'message': _chase_status_message(run, payload['browser_engine']),
                'account_choices': run.account_choices or [],
                'imported_count': run.imported_count,
                'duplicate_count': run.duplicate_count,
            }
        else:
            payload['run'] = None
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @expose('/chase/diagnostics/<int:run_id>/', methods=('GET',))
    def chase_browser_diagnostics(self, run_id):
        """Admin-only, encrypted-at-rest passkey evidence without bank data."""
        run = (db.session.query(ChaseBrowserRuns)
               .join(Connections, ChaseBrowserRuns.connection_id == Connections.id)
               .filter(ChaseBrowserRuns.id == run_id,
                       ChaseBrowserRuns.mode == 'passkey_enroll',
                       Connections.source == 'chase_browser')
               .one_or_none())
        if run is None:
            abort(404)
        report = json.loads(run.diagnostics) if run.diagnostics else None
        response = jsonify({'run_id': run.id, 'status': run.status,
                            'error_code': run.error_code,
                            'report': report})
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response


_CHASE_ERRORS = {
    'chase_signin_error': 'Chase reported a sign-in error. Its Firefox tab is still open so you can read the message.',
    'firefox_session_busy': 'Firefox still has an automation session that Pacioli cannot reconnect to. Quit Firefox normally, reopen Firefox for Pacioli, and retry.',
    'browser_mode_mismatch': 'This test belongs to a different browser. Use Check Firefox connection for Firefox.',
    'firefox_not_connected': 'Firefox is not connected. Quit Firefox when ready, then start it with the Pacioli Firefox launcher and try again.',
    'firefox_disconnected': 'The Firefox connection ended. Your browser and other tabs were not intentionally closed.',
    'firefox_command_timeout': 'Firefox did not respond in time. Check its Chase tab and retry.',
    'firefox_command_failed': 'Firefox could not complete the browser action. Check its Chase tab and retry.',
    'firefox_page_changed': 'The Firefox page changed during an action. The connector stopped; retry when ready.',
    'firefox_control_unavailable': 'A Chase control could not be identified in Firefox. The connector stopped.',
    'firefox_download_incomplete': 'Firefox did not finish the QFX download.',
    'firefox_download_timeout': 'The QFX download did not finish in time. Complete any Firefox Save As dialog before retrying.',
    'unexpected_download_host': 'The download came from an unexpected address. It was not imported.',
    'ambiguous_download': 'More than one download started in the Chase tab. No file was selected for import.',
    'browser_not_requested': 'This sync is not waiting for a verification window.',
    'browser_profile_busy': 'The previous browser is still closing. Try again shortly.',
    'browser_profile_unavailable': 'Pacioli could not open its private browser profile.',
    'browser_window_closed': 'The Chase browser closed before this session finished. Start again when ready.',
    'provider_not_ready': 'Wait until Pacioli detects your Chase accounts before confirming passkey sign-in.',
    'provider_accounts_lost': 'The accounts-page indicator disappeared before confirmation. The test profile was discarded.',
    'provider_confirmation_timeout': 'Passkey sign-in was not confirmed. The test profile was discarded.',
    'verification_window_timeout': 'The verification window was not requested in time. Start a new sync when ready.',
    'review_usd_required': 'Confirm that the Chase accounts are yours and use USD.',
    'credentials_missing': 'Save a Chase login before starting.',
    'credentials_required': 'Enter both Chase username and password.',
    'invalid_credentials': 'Use a username of up to 254 characters and a password of up to 512 characters.',
    'another_run_active': 'A Chase browser session is already running.',
    'not_choosing_accounts': 'The connector is not waiting for an account selection.',
    'invalid_account_selection': 'Select one or more listed Chase accounts.',
    'manual_run_required': 'Complete one supervised Chase export to approve accounts before using Sync now.',
    'mfa_code_not_requested': 'The Chase browser is not requesting a one-time code.',
    'passkey_not_ready': 'Wait until Chase creates the passkey and shows its success screen.',
    'chase_passkey_rejected': 'Chase rejected the passkey. Pacioli saved no key; review the diagnostic report for browser and response details.',
    'passkey_missing': 'Add a local passkey before testing sign-in.',
    'passkey_already_saved': 'A local passkey is already saved. Remove the old passkey at Chase before replacing its local copy.',
    'passkey_enrollment_unavailable': 'Local software passkey enrollment is disabled after Chase rejected the supervised tests on this workstation.',
    'passkey_signin_unavailable': 'The fresh browser did not complete a passkey sign-in. Use a visible session.',
    'invalid_passkey': 'The local passkey is invalid. Remove it and enroll again.',
    'multiple_passkeys_created': 'The browser created multiple passkey candidates. Pacioli saved none. Check Chase’s passkey list before another attempt.',
    'passkey_enrollment_timeout': 'Passkey enrollment was not confirmed in time.',
    'invalid_mfa_code': 'Enter the 4–12 digit code Chase provided.',
    'interactive_verification_needed': 'Chase needs an interactive verification. Start a supervised session here.',
    'account_selection_changed': 'Chase account choices changed. Start a supervised session to review them.',
    'desktop_launch_failed': 'Could not start the workstation browser.',
    'worker_interrupted': 'The browser worker stopped before finishing. Start a new session.',
    'scratch_unavailable': 'The private temporary directory is unavailable.',
    'browser_unavailable': 'Chromium is unavailable on the workstation.',
    'browser_version_unknown': 'Could not identify the Chromium version.',
    'chase_signed_out': 'Chase ended the session. The connector stopped without retrying.',
    'unexpected_browser_host': 'The browser left Chase. The connector stopped.',
    'unexpected_login_host': 'The login form was not hosted by Chase. The connector stopped.',
    'mfa_timeout': 'Chase sign-in or MFA was not completed in time.',
    'account_choice_timeout': 'No Chase accounts were selected in time.',
    'not_qfx_download': 'Chase did not provide a QFX file.',
    'qfx_size_invalid': 'The downloaded QFX file was empty or too large.',
    'some_imports_failed': 'Some Chase files could not be imported. Review the imported accounts.',
    'browser_or_import_failed': 'The Chase browser or QFX import failed. No further account was processed.',
}


def _chase_message(code):
    return _CHASE_ERRORS.get(code, 'The Chase connector could not continue.')


def _chase_status_message(run, browser_engine='chromium'):
    if browser_engine == 'firefox':
        if run.status == 'queued':
            return 'Connecting to your Firefox and opening a Chase tab.'
        if run.status == 'signing_in':
            return 'Checking Chase in your existing Firefox profile.'
        if run.status == 'waiting_for_mfa' and run.mfa_prompt != 'code':
            if run.mfa_prompt == 'open_browser':
                return 'Chase needs your help in Firefox. Click Open verification window to bring its tab forward.'
            return 'Complete Chase or LastPass verification in Pacioli’s Chase tab in Firefox. This page updates automatically.'
        if run.status == 'exporting' and run.mfa_prompt == 'download':
            return 'Downloading QFX in Firefox. If Firefox shows a Save As dialog, save the file to continue.'
    if run.mode == 'firefox_test' and run.status == 'completed':
        return 'Firefox reached your Chase accounts. No transactions were downloaded. This checks session access, not which sign-in method was used.'
    if run.status == 'queued':
        return ('Opening LastPass setup and Chase in a fresh window.' if run.mode == 'lastpass_test' else
                'Opening a fresh Chase window for your existing passkey.' if run.mode == 'provider_test' else
                'Opening Chase for passkey enrollment.' if run.mode == 'passkey_enroll' else
                'Testing the saved passkey in a fresh Chase browser.' if run.mode == 'passkey_test' else
                'Starting the on-demand Chase sync.' if run.mode == 'automatic' else
                'Starting the Chase browser on this workstation.')
    if run.status == 'signing_in':
        return 'Checking Chase sign-in in the workstation browser.'
    if run.status == 'waiting_for_mfa':
        if run.mfa_prompt == 'open_browser':
            return 'Chase needs your help. Click Open verification window to continue this sync on the workstation.'
        if run.mode in ('provider_test', 'lastpass_test'):
            if run.provider_confirmed:
                return 'Your passkey confirmation was received. Waiting for Chase accounts before saving the session; keep the Chase tab open.'
            if run.mfa_prompt == 'provider_review':
                return 'Chase accounts detected. Confirm below only if you signed in using your existing passkey.'
            if run.mode == 'lastpass_test':
                return 'Install and unlock LastPass in the separate Chromium window, then return to its Chase tab and sign in using your saved passkey. Keep the Chase tab open.'
            return 'Choose passkey sign-in in the Chase window. Use your existing provider or phone and complete its approval there.'
        if run.mode == 'passkey_enroll' and run.mfa_prompt == 'passkey_setup':
            return 'In the visible Chase browser, add a passkey in Chase security settings. Keep this page open.'
        if run.mode == 'passkey_enroll' and run.mfa_prompt == 'passkey_review':
            return 'The browser created a passkey candidate. Confirm only if Chase says it was added successfully; otherwise cancel.'
        if run.mfa_prompt == 'code':
            return ('Chase requested a one-time code. Enter it below.' if run.mode == 'automatic'
                    else 'Chase requested a one-time code. Enter it below or directly in the Chase window.')
        if run.browser_visible:
            return 'Complete verification in the Chase window. This sync resumes automatically when your accounts appear.'
        if run.mode in ('automatic', 'passkey_test'):
            return 'Checking Chase sign-in. Pacioli will ask here if a verification window is needed.'
        return 'Chase needs verification in the visible browser.'
    if run.status == 'choosing_accounts':
        return 'Choose the Chase accounts to export below.'
    if run.status == 'exporting':
        return 'Downloading QFX from Chase.'
    if run.status == 'importing':
        return 'Importing the downloaded QFX into encrypted Pacioli storage.'
    if run.status == 'completed':
        if run.mode in ('provider_test', 'lastpass_test'):
            return 'Chase sign-in succeeded and you confirmed using your existing passkey. The browser session was saved. No QFX was downloaded.'
        if run.mode == 'passkey_enroll':
            return 'Local Chase passkey saved encrypted. Test it in a fresh browser before relying on it.'
        if run.mode == 'passkey_test':
            return 'The local passkey signed in to Chase in a fresh browser. No QFX was downloaded.'
        return ('Finished: {} new file(s) imported; {} already present. '
                'No journal entries were posted.').format(
                    run.imported_count, run.duplicate_count)
    if run.status == 'partial':
        return ('{} new file(s) imported; {} already present. {}').format(
            run.imported_count, run.duplicate_count,
            _chase_message(run.error_code))
    if run.status == 'cancelled':
        return 'Chase browser session cancelled.'
    if run.status == 'needs_attention':
        return _chase_message(run.error_code)
    return _chase_message(run.error_code)

admin.add_view(ConnectionsModelView(Connections, db.session, category='Admin'))


class ConnectionResponsesView(PrivateModelView):
    can_create = False
    can_delete = False
    can_edit = False
    # column_default_sort = {'field': 'connected_at', 'sort_desc': True, 'absolute_value': False}
    column_default_sort = ('connected_at', True)
    column_list = ('id', 'connection', 'connected_at')
    column_details_list = ('id', 'connection', 'connected_at', 'response')
    column_sortable_list = column_list
    column_filters = column_list
    column_labels = dict(id='ID')
    column_display_actions = False
    column_formatters = dict(connected_at=date_formatter)

admin.add_view(ConnectionResponsesView(ConnectionResponses, db.session, category='Admin', endpoint='connection-responses'))


class MappingsModelView(PrivateModelView):
    column_labels = dict(id='ID', match_mode='Match', review_required='Needs review')
    form_choices = dict(source=[('ofx', 'ofx')],
                        match_mode=[('keyword', 'Keyword terms'), ('exact', 'Exact description')])
    column_display_all_relations = True
    column_list = ('id', 'source', 'keyword', 'match_mode', 'review_required', 'positive_debit_subaccount',
                   'positive_credit_subaccount', 'negative_debit_subaccount', 'negative_credit_subaccount')
    form_columns = column_list + ('review_note',)
    column_sortable_list = column_list
    column_filters = ('id', 'source', 'keyword', 'match_mode', 'review_required')
    can_view_details = True
    column_details_list = column_list + ('review_note', 'provenance')
    column_searchable_list = ('keyword', )
    subaccount_loader = dict(fields=('name',), page_size=10, placeholder='-')
    form_ajax_refs = dict(positive_debit_subaccount=subaccount_loader, positive_credit_subaccount=subaccount_loader,
                          negative_debit_subaccount=subaccount_loader, negative_credit_subaccount=subaccount_loader)
    column_editable_list = ('keyword',)
admin.add_view(MappingsModelView(Mappings, db.session, category='Admin'))


class MappingOverlapsModelView(PrivateModelView):
    can_create = False
    can_delete = False
    can_edit = False
    column_display_actions = False
    column_labels = dict(mapping_id_1='Mapping ID 1', mapping_id_2='Mapping ID 2')
    column_formatters = dict(mapping_id_1=link_mapping_formatter, mapping_id_2=link_mapping_formatter,
                             mapping_keyword_1=link_transaction_search_formatter, mapping_keyword_2=link_transaction_search_formatter)
admin.add_view(MappingOverlapsModelView(MappingOverlaps, db.session, category='Admin', name='Mapping Overlaps', endpoint='mapping-overlaps'))
