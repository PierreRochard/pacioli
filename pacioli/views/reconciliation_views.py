"""Authenticated, CSRF-protected accounting evidence and review workbench."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

from flask import request, redirect, url_for, flash, abort, make_response, jsonify
from flask_admin import expose
from flask_security import current_user
from sqlalchemy import text
from pacioli.extensions import admin, db
from pacioli.views import PrivateModelView, require_action_csrf
from pacioli.models import (BankStatements, OpeningBalances, AccountingEvidence,
    AmazonEvidenceBundles, AccountingReviews, AccountingEvaluationCases, AmazonCollectionRuns)
from pacioli.functions import reconciliation as rec
from pacioli.functions import amazon_evidence as amazon
from pacioli.functions import amazon_csv
from pacioli.functions import amazon_reconciliation as archive_match
from pacioli.functions import accounting_review as reviews
from pacioli.functions import snapshot_reconciliation as snapshot_review


class ReconciliationView(PrivateModelView):
    can_create = can_edit = can_delete = can_export = False
    can_view_details = False

    def create_blueprint(self, admin):
        blueprint = super().create_blueprint(admin)

        @blueprint.after_request
        def keep_policy_answers_private(response):
            # Include authentication redirects and CSRF/validation failures.
            if request.endpoint in {self.endpoint + '.amazon_policy_questions',
                                    self.endpoint + '.amazon_policy_history'}:
                response.headers['Cache-Control'] = 'private, no-store'
                response.headers['X-Content-Type-Options'] = 'nosniff'
            return response

        return blueprint

    def page(self, **kwargs):
        response=make_response(self.render('reconciliation.html', **kwargs))
        response.headers['Cache-Control']='no-store'
        return response

    @expose('/')
    def index_view(self):
        accounts=db.session.execute(text('''SELECT a.id,a.name,min(t.date)::date AS first_date
            FROM ofx.acctfrom a LEFT JOIN ofx.transactions t ON t.account_id=a.id GROUP BY a.id,a.name ORDER BY a.id''')).all()
        preview=None
        if request.args.get('account'):
            try:
                preview=rec.opening_preview(int(request.args['account']),date.fromisoformat(request.args['cutover']))
            except (ValueError,KeyError) as exc:
                flash(str(exc),'error')
        from pacioli.functions.accounting_report import bank_balance_differences
        snapshot_keys, snapshot_reviews = snapshot_review.reviewed(db.session)
        snapshots = bank_balance_differences(db.session, include_reviewed=True)
        for item in snapshots:
            item['reviewed'] = (item['account_id'], item['as_of'], item.get('bank_balance')) in snapshot_keys
            item['candidate'] = None if item['reviewed'] or 'bank_balance' not in item else snapshot_review.candidate(
                item['account_id'], item['as_of'], item['bank_balance'])
        return self.page(tab='statements',accounts=accounts,checks=rec.statement_checks(),preview=preview,
            openings={r.account_id:r for r in db.session.query(OpeningBalances)},
            snapshots=snapshots,snapshot_reviews=snapshot_reviews)

    @expose('/snapshot/',methods=('POST',))
    def snapshot(self):
        require_action_csrf()
        try:
            snapshot_review.approve(int(request.form['original_id']),int(request.form['witness_id']),
                int(request.form['statement_id']),request.form['digest'],current_user.email,request.form['reason'])
            db.session.commit();flash('Snapshot cutoff evidence saved; original balances and journals are unchanged.','success')
        except (ValueError,KeyError,TypeError) as exc:
            db.session.rollback();flash(str(exc),'error')
        return redirect(url_for('.index_view'))

    @expose('/statement/',methods=('POST',))
    def statement(self):
        require_action_csrf()
        try:
            metadata={key:request.form[key] for key in ('account_id','start','end','opening','closing','movement','verification')}
            metadata['account_id']=int(metadata['account_id'])
            rec.import_statement(metadata,request.files['pdf'].read(),current_user.email)
            db.session.commit();flash('Statement evidence saved.','success')
        except (ValueError,KeyError,TypeError) as exc:
            db.session.rollback();flash(str(exc),'error')
        return redirect(url_for('.index_view'))

    @expose('/document/<int:identifier>/')
    def document(self,identifier):
        row=db.session.query(AccountingEvidence).get(identifier)
        if not row or row.kind!='bank_statement':abort(404)
        import base64
        pdf=base64.b64decode(json.loads(row.payload)['pdf'])
        response=make_response(pdf)
        response.headers.update({'Content-Type':'application/pdf','Cache-Control':'no-store',
            'Content-Disposition':'attachment; filename="statement-{}.pdf"'.format(identifier),
            'X-Content-Type-Options':'nosniff'})
        return response

    @expose('/opening/',methods=('POST',))
    def opening(self):
        require_action_csrf()
        try:
            rec.post_opening(int(request.form['account']),date.fromisoformat(request.form['cutover']),request.form['digest'],current_user.email)
            db.session.commit();flash('Opening balance adopted; statement balances agree.','success')
        except (ValueError,KeyError) as exc:
            db.session.rollback();flash(str(exc),'error')
        return redirect(url_for('.index_view'))

    @expose('/transfers/')
    def transfers(self):
        pairs=rec.transfer_candidates()
        return self.page(tab='transfers',pairs=pairs,unresolved=rec.unresolved_transfers(pairs),
            resolved=len(rec.resolved_transfer_ids(db.session)))

    @expose('/transfer/',methods=('POST',))
    def transfer(self):
        require_action_csrf()
        try:
            rec.match_transfer(int(request.form['left']),int(request.form['right']),request.form['digest'],current_user.email,request.form['reason'])
            db.session.commit();flash('Transfer linked; original journals retained.','success')
        except (ValueError,KeyError) as exc:
            db.session.rollback();flash(str(exc),'error')
        return redirect(url_for('.transfers'))

    @expose('/schwab/share-moves/',methods=('GET','POST'))
    def share_moves(self):
        """Owner approval of local Qwen memo proposals; other moves stay flagged."""
        from pacioli.functions import share_move_advisor as moves
        if request.method=='POST':
            require_action_csrf()
            try:
                reason=request.form.get('reason','').strip()
                if not reason:
                    raise ValueError('Say why these rows have no economic effect')
                moves.approve(db.session,int(request.form['proposal']),current_user.email,reason)
                db.session.commit();flash('Memo rule saved; matching share moves now post nothing.','success')
            except (ValueError,KeyError) as exc:
                db.session.rollback();flash(str(exc),'error')
            return redirect(url_for('.share_moves'))
        return self.page(tab='share_moves',groups=moves.pending(db.session))

    @expose('/amazon/',methods=('GET','POST'))
    def amazon(self):
        preview=None
        if request.method=='POST':
            require_action_csrf()
            action=None
            try:
                action=request.form['action']
                if action=='import':
                    rows=amazon.import_items_csv(request.files['csv'].read(),current_user.email)
                    db.session.commit();flash('{} shipment/refund bundles saved.'.format(len(rows)),'success')
                elif action=='import_archive':
                    uploaded=request.files['archive']
                    row,created=amazon_csv.import_archive(uploaded.read(amazon_csv.MAX_UPLOAD+1),
                        uploaded.filename or '',current_user.email)
                    db.session.commit()
                    manifest=json.loads(row.payload)
                    flash(('Amazon CSV evidence saved: {} files, {} rows. Review settlements before booking.'
                        .format(len(manifest['tables']),sum(t['rows'] for t in manifest['tables'])))
                        if created else 'This exact Amazon export is already saved; no rows were duplicated.','success')
                elif action=='reconcile_archive':
                    row=archive_match.build_and_save(int(request.form['archive_id']),current_user.email)
                    db.session.commit()
                    flash('Amazon settlement review saved. Candidates require evidence and category review before booking.','success')
                    return redirect(url_for('.amazon',reconciliation_id=row.id))
                elif action=='preview':
                    allocations=[dict(category=c,amount=v) for c,v in zip(request.form.getlist('category'),request.form.getlist('amount')) if c or v]
                    if len(request.form.getlist('category'))!=len(request.form.getlist('amount')):
                        raise ValueError('An amount is required for each category')
                    preview=amazon.allocation_preview(int(request.form['bundle']),request.form['transaction'],allocations)
                elif action=='book':
                    amazon.book_allocation(json.loads(request.form['preview']),current_user.email,request.form['reason'])
                    db.session.commit();flash('Existing bank settlement allocated to its item evidence.','success')
                elif action=='collect':
                    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.amazon_worker'))"))
                    expired=datetime.now(timezone.utc)-timedelta(minutes=5)
                    for old in db.session.query(AmazonCollectionRuns).filter_by(state='running'):
                        if old.updated_at<expired:
                            old.state='needs_attention';old.progress='Worker interrupted; already saved receipts are retained'
                        else:raise ValueError('An Amazon collection is already running')
                    db.session.flush()
                    run=AmazonCollectionRuns(state='running',progress='Waiting for the selected Amazon tab',reviewer=current_user.email)
                    db.session.add(run);db.session.commit()
                    root=Path(__file__).resolve().parents[2]
                    try:
                        subprocess.Popen([sys.executable,str(root/'scripts/collect_amazon.py'),str(run.id)],cwd=str(root),
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            start_new_session=True,close_fds=True)
                    except OSError:
                        run.state='needs_attention';run.progress='Could not start collection';db.session.commit()
                elif action=='receipt_items':
                    import csv,io
                    doc=db.session.query(AccountingEvidence).get(int(request.form['receipt']))
                    if not doc or doc.kind!='amazon_receipt':raise ValueError('Select captured receipt evidence')
                    raw=json.loads(doc.payload)
                    values=[request.form.getlist(k) for k in ('line','kind','title','line_amount')]
                    if len({len(v) for v in values})!=1 or not values[0]:raise ValueError('Complete every item line')
                    stream=io.StringIO();writer=csv.writer(stream);writer.writerow(amazon.FIELDS)
                    for line,kind,title,amount in zip(*values):
                        if title not in raw['text']:raise ValueError('Each item title must occur in the captured receipt')
                        writer.writerow([request.form['bundle_id'],line,raw['order_id'],request.form['date'],kind,title,amount,'USD'])
                    bundles=amazon.import_items_csv(stream.getvalue().encode(),current_user.email)
                    rec.evidence('amazon_receipt_review',dict(receipt_id=doc.id,bundle_ids=[b.id for b in bundles],
                        reason=request.form['reason']),current_user.email)
                    db.session.commit();flash('Reviewed receipt lines saved; choose the matching bank settlement below.','success')
                else:raise ValueError('Unknown action')
            except (ValueError,KeyError,TypeError) as exc:
                db.session.rollback();flash(str(exc),'error')
            # Polling refreshes must reload a GET, never resubmit collection or booking.
            if action!='preview':
                return redirect(url_for('.amazon'))
        bundles=[dict(id=b.id,body=json.loads(b.details),transaction=b.transaction_id,
                      candidates=amazon.candidates(b) if not b.transaction_id else [])
                 for b in db.session.query(AmazonEvidenceBundles).order_by(AmazonEvidenceBundles.id.desc()).limit(100)]
        from pacioli.functions.accounting_report import chart_accounts
        categories=sorted(name for name,(element,cash) in chart_accounts(db.session).items()
                          if element in ('Expenses','Assets') and not cash and name not in ('Bookkeeping Suspense','Transfer Clearing'))
        runs=db.session.query(AmazonCollectionRuns).order_by(AmazonCollectionRuns.id.desc()).limit(3).all()
        receipts=[dict(id=d.id,body=json.loads(d.payload)) for d in db.session.query(AccountingEvidence).filter_by(kind='amazon_receipt').order_by(AccountingEvidence.id.desc()).limit(100)]
        archives=[dict(id=d.id,body=json.loads(d.payload)) for d in db.session.query(AccountingEvidence)
                  .filter_by(kind='amazon_csv_import').order_by(AccountingEvidence.id.desc()).limit(20)]
        try:
            archive_review=archive_match.review_page(identifier=request.args.get('reconciliation_id'),
                status=request.args.get('match_status',''),page=request.args.get('match_page','1'))
        except ValueError:
            abort(400)
        return self.page(tab='amazon',bundles=bundles,preview=preview,categories=categories,runs=runs,
            receipts=receipts,archives=archives,archive_review=archive_review)

    @expose('/amazon/reconciliation/<int:identifier>/csv/')
    def amazon_reconciliation_csv(self,identifier):
        part=request.args.get('part','records')
        try:content=archive_match.report_csv(identifier,part=part)
        except ValueError:abort(404)
        response=self.amazon_download(content,'text/csv; charset=utf-8',
            'amazon-settlement-review-{}-{}.csv'.format(identifier,part))
        response.headers['Cache-Control']='private, no-store'
        return response

    @expose('/amazon/items/', methods=('GET', 'POST'))
    def amazon_items(self):
        from pacioli.functions import amazon_item_workflow as item_workflow
        from pacioli.functions import amazon_item_posting as item_posting

        def identifier(value, label):
            value = str(value).strip()
            if (not value.isascii() or not value.isdigit() or len(value) > 10
                    or not 0 < int(value) <= 2147483647):
                raise ValueError('Choose a valid ' + label)
            return int(value)

        selected_run = None
        selected_page = 1
        selected_item = None
        posting_saved = False
        if request.method == 'POST':
            require_action_csrf()
            try:
                action = request.form.get('action', '')
                selected_page = identifier(request.form.get('page', '1'), 'page')
                if action == 'start':
                    archive_id = identifier(request.form.get('archive_id', ''), 'source archive')
                    run = item_workflow.create_run(archive_id, current_user.email, think=False)
                    db.session.commit()
                    selected_run = run.id
                    self._start_amazon_item_worker(selected_run)
                    flash('Local item classification started. Suggestions do not post journal entries.', 'success')
                else:
                    selected_run = identifier(request.form.get('run_id', ''), 'classification run')
                    if action == 'resume':
                        item_workflow.run_status(selected_run)
                        db.session.commit()
                        self._start_amazon_item_worker(selected_run)
                        flash('Classification resume requested; already saved results are retained.', 'success')
                    elif action == 'review':
                        posting = self._amazon_item_posting_status(selected_run)
                        if posting and posting['state'] in ('queued', 'running'):
                            raise ValueError('Posting is in progress. Review changes after it finishes.')
                        selected_item = identifier(request.form.get('job_id', ''), 'item')
                        category = request.form.get('category', '').strip()
                        if category == '__choose__':
                            raise ValueError('Choose a reviewed category or keep the item in suspense')
                        reason = request.form.get('reason', '').strip()
                        if not reason or len(reason) > 2000:
                            raise ValueError('Provide a review reason of 1 to 2,000 characters')
                        item_workflow.review_item(selected_item,
                            None if category in ('', '__suspense__') else category,
                            reason, current_user.email)
                        db.session.commit()
                        flash('Item decision saved. Use Post reviewed allocations to update the ledger.', 'success')
                    elif action == 'post':
                        reason = request.form.get('reason', '').strip()
                        if not reason or len(reason) > 2000:
                            raise ValueError('Provide a posting reason of 1 to 2,000 characters')
                        job = item_posting.create_request(selected_run, request.form.get('review_digest', ''),
                                                          current_user.email, reason)
                        db.session.commit()
                        posting_saved = True
                        self._start_amazon_item_posting_worker(job.id)
                        flash('Reviewed posting request saved. The background worker will apply it; follow posting status below.', 'success')
                    else:
                        raise ValueError('Unknown item classification action')
            except OSError:
                db.session.rollback()
                flash('The posting request is saved, but its background worker could not start. '
                      'Submit Post reviewed allocations again to retry the saved request.' if posting_saved else
                      'The run is saved, but its background worker could not start. Use Resume classification to retry.', 'error')
            except (ValueError, KeyError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), 'error')
            return redirect(url_for('.amazon_items', run_id=selected_run, page=selected_page,
                                    _anchor='item-{}'.format(selected_item) if selected_item else None))

        try:
            if request.args.get('run_id') is not None:
                selected_run = identifier(request.args['run_id'], 'classification run')
            selected_page = identifier(request.args.get('page', '1'), 'page')
            context = item_workflow.page(run_id=selected_run, page=selected_page)
            context['posting_job'] = self._amazon_item_posting_status(context['run']['id']) if context['run'] else None
        except (ValueError, KeyError, TypeError):
            abort(400)
        response = make_response(self.render('amazon_item_review.html', **context))
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @expose('/amazon/suspense/', methods=('GET', 'POST'))
    def amazon_suspense(self):
        from pacioli.functions import amazon_suspense as suspense

        def identifier(value, label):
            value = str(value).strip()
            if (not value.isascii() or not value.isdigit() or len(value) > 10
                    or not 0 < int(value) <= 2147483647):
                raise ValueError('Choose a valid ' + label)
            return int(value)

        def bounded(value, label, limit=2000, required=True):
            value = str(value).strip()
            if (required and not value) or len(value) > limit:
                raise ValueError('Provide a valid {} ({} characters maximum)'.format(label, limit))
            return value

        def item_identifier(value):
            value = bounded(value, 'item', 128)
            if not value.isascii() or any(not (c.isalnum() or c in '-_') for c in value):
                raise ValueError('Choose a valid item')
            return value

        selected_plan, selected_page = None, 1
        selected_group, selected_category = '', ''
        if request.method == 'POST':
            require_action_csrf()
            try:
                selected_plan = identifier(request.form.get('plan_id', ''), 'review plan')
                selected_page = identifier(request.form.get('page', '1'), 'page')
                selected_group = bounded(request.form.get('filter_group', ''), 'review filter', 64, False)
                selected_category = bounded(request.form.get('filter_category', ''), 'category filter', 200, False)
                action = request.form.get('action', '')
                if action == 'prepare':
                    suspense.prepare_new_review_run(selected_plan, current_user.email, think=False)
                    db.session.commit()
                    flash('Current-policy review prepared. Previously posted item approvals are retained '
                          'where their source evidence still matches. No model or posting was started.', 'success')
                elif action == 'fact':
                    item_id = item_identifier(request.form.get('item_id', ''))
                    note = bounded(request.form.get('note', ''), 'purchase context')
                    token = bounded(request.form.get('token', ''), 'review version', 128)
                    suspense.save_fact(selected_plan, item_id, note, token, current_user.email)
                    db.session.commit()
                    flash('Purchase context saved as a new evidence note. Review its classification next.', 'success')
                elif action == 'review':
                    selected = request.form.getlist('item_id')
                    if not selected or len(selected) > 100 or len(set(selected)) != len(selected):
                        raise ValueError('Select between 1 and 100 different items to review')
                    item_ids = [item_identifier(value) for value in selected]
                    if len(set(item_ids)) != len(item_ids):
                        raise ValueError('Select each item only once')
                    category = bounded(request.form.get('category', ''), 'reviewed category', 200)
                    if category == '__choose__':
                        raise ValueError('Choose a reviewed category or keep the selected items in suspense')
                    category = None if category == '__suspense__' else category
                    reason = bounded(request.form.get('reason', ''), 'review reason')
                    token = bounded(request.form.get('token', ''), 'review version', 128)
                    remember = request.form.get('remember_product', '')
                    if remember not in ('', '1') or (remember and category is None):
                        raise ValueError('Remembered product suggestions need a reviewed category')
                    suspense.apply_reviews(selected_plan, item_ids, category, reason, token,
                                           current_user.email, remember_product=remember == '1')
                    db.session.commit()
                    flash('Reviewed classifications saved. The ledger changes only after the separate '
                          'Post reviewed allocations action.', 'success')
                else:
                    raise ValueError('Unknown suspense review action')
            except (ValueError, KeyError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), 'error')
            response = redirect(url_for('.amazon_suspense', plan_id=selected_plan, page=selected_page,
                                        group=selected_group, category=selected_category))
            response.headers['Cache-Control'] = 'private, no-store'
            return response

        try:
            if request.args.get('plan_id') is not None:
                selected_plan = identifier(request.args['plan_id'], 'review plan')
            selected_page = identifier(request.args.get('page', '1'), 'page')
            selected_group = bounded(request.args.get('group', ''), 'review filter', 64, False)
            selected_category = bounded(request.args.get('category', ''), 'category filter', 200, False)
            context = suspense.page(plan_id=selected_plan, group=selected_group,
                                    category=selected_category, page=selected_page)
            context.update(selected_group=selected_group, selected_category=selected_category)
        except (ValueError, KeyError, TypeError):
            abort(400)
        response = make_response(self.render('amazon_suspense.html', **context))
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @expose('/amazon/policies/', methods=('GET', 'POST'))
    def amazon_policy_questions(self):
        from pacioli.functions import amazon_policy_application as application

        def scalar(values, name, default=''):
            if len(values.getlist(name)) > 1:
                raise ValueError('Supply each classification field only once.')
            return values.get(name, default)

        def bounded(value, label, limit=2000, required=True):
            value = str(value).strip()
            if (required and not value) or len(value) > limit:
                raise ValueError('Provide a valid {} ({} characters maximum).'.format(label, limit))
            return value

        def identifier(value, label):
            value = bounded(value, label, 10)
            if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2147483647:
                raise ValueError('Choose a valid {}.'.format(label))
            return int(value)

        def item_key(value):
            value = bounded(value, 'purchase', 128)
            if not value.isascii() or any(not (char.isalnum() or char in '-_') for char in value):
                raise ValueError('Choose a valid purchase.')
            return value

        selected_plan, selected_page, submitted = None, 1, {}

        def render_batch(status=200):
            context = application.page(plan_id=selected_plan)
            all_items = context.get('items', [])
            total = len(all_items)
            pages = max(1, (total + 99) // 100)
            if selected_page > pages:
                raise ValueError('Choose a page from the current classification review.')
            first = (selected_page - 1) * 100
            context.update(items=all_items[first:first + 100], batch_page=selected_page,
                batch_pages=pages, batch_total=total, batch_first=first + 1 if total else 0,
                batch_last=min(first + 100, total))
            context['batch_has_classifications'] = any(
                (submitted[item['item_id']]['category'] if item['item_id'] in submitted else item.get('category'))
                in item.get('allowed_categories', []) for item in context['items'])
            context['submitted'] = submitted
            return make_response(self.render('amazon_policy_batch.html', **context), status)

        if request.method == 'POST':
            require_action_csrf()
            try:
                if scalar(request.form, 'action') != 'save':
                    raise ValueError('Choose Save classifications to review this batch.')
                selected_plan = identifier(scalar(request.form, 'plan_id'), 'suspense plan')
                selected_page = identifier(scalar(request.form, 'page', '1'), 'classification page')
                token = bounded(scalar(request.form, 'token'), 'review version', 128)
                selected = request.form.getlist('item_id')
                if not selected or len(selected) > 100:
                    raise ValueError('Review between 1 and 100 purchases in one batch.')
                item_ids = [item_key(value) for value in selected]
                if len(set(item_ids)) != len(item_ids):
                    raise ValueError('Review each purchase only once.')
                allowed_fields = {'csrf_token', 'action', 'plan_id', 'page', 'token', 'item_id'}
                selections = []
                for item_id in item_ids:
                    category_name, note_name = 'category_' + item_id, 'note_' + item_id
                    allowed_fields.update((category_name, note_name))
                    category = bounded(scalar(request.form, category_name), 'category', 200)
                    note = bounded(scalar(request.form, note_name), 'classification note', 1000, False)
                    category = None if category == '__suspense__' else category
                    submitted[item_id] = dict(category=category, note=note)
                    selections.append(dict(item_id=item_id, category=category, reason=note))
                if set(request.form) - allowed_fields:
                    raise ValueError('Unexpected classification fields; reload and review the current batch.')
                # The visible table is the exact editable preview. Freeze and
                # apply that explicit submission atomically; posting stays a
                # separate approval through the existing ledger workflow.
                preview_id = application.prepare_preview(selected_plan, selections, token, current_user.email)
                preview = application.preview_page(preview_id, current_user.email)
                result = application.apply_preview(preview_id, preview['token'], current_user.email)
                db.session.commit()
                if not any(item['category'] for item in selections):
                    flash('Notes and suspense decisions saved. No journal entries changed.', 'success')
                    return redirect(url_for('.amazon_policy_questions', plan_id=selected_plan, page=selected_page))
                flash('Classifications saved. Review the exact allocation totals below before posting.', 'success')
                return redirect(url_for('.amazon_items', run_id=result['run_id'], _anchor='post-items-heading'))
            except (ValueError, KeyError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), 'error')
                if selected_plan:
                    try:
                        return render_batch(status=400)
                    except (ValueError, KeyError, TypeError):
                        db.session.rollback()
                return make_response(self.render('amazon_policy_batch.html', plan=None,
                    invalid_request=True, submitted=submitted), 400)

        try:
            if scalar(request.args, 'plan_id'):
                selected_plan = identifier(scalar(request.args, 'plan_id'), 'suspense plan')
            selected_page = identifier(scalar(request.args, 'page', '1'), 'classification page')
            # Existing answer receipts and question bookmarks remain valid.
            preview_id = scalar(request.args, 'preview_id')
            question_key = scalar(request.args, 'question_key')
            if preview_id or question_key:
                if preview_id:
                    preview_id = identifier(preview_id, 'answer preview')
                if question_key:
                    question_key = item_key(question_key)
                return redirect(url_for('.amazon_policy_history', plan_id=selected_plan,
                    preview_id=preview_id or None, question_key=question_key or None))
            return render_batch()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()
            abort(400)

    @expose('/amazon/policies/history/', methods=('GET', 'POST'))
    def amazon_policy_history(self):
        from pacioli.functions import amazon_policy_questions as policies

        def bounded(value, label, limit=2000, required=True):
            value = str(value).strip()
            if (required and not value) or len(value) > limit:
                raise ValueError('Provide a valid {} ({} characters maximum).'.format(label, limit))
            return value

        def identifier(value, label):
            value = bounded(value, label, 10)
            if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2147483647:
                raise ValueError('Choose a valid {}.'.format(label))
            return int(value)

        def key(value, label):
            value = bounded(value, label, 128)
            if not value.isascii() or any(not (char.isalnum() or char in '-_') for char in value):
                raise ValueError('Choose a valid {}.'.format(label))
            return value

        def scalar(values, name, default=''):
            if len(values.getlist(name)) > 1:
                raise ValueError('Supply each answer field only once.')
            return values.get(name, default)

        selected_plan, selected_question = None, None
        form_values = dict(choice='', scope='', item_ids=[], custom_text='',
                           threshold_amount='', threshold_basis='')

        def render_question(status=200):
            context = policies.page(plan_id=selected_plan, question_key=selected_question)
            context['form_values'] = form_values
            return make_response(self.render('amazon_policy_questions.html', **context), status)

        if request.method == 'POST':
            require_action_csrf()
            try:
                action = scalar(request.form, 'action')
                if action == 'confirm':
                    preview_id = identifier(scalar(request.form, 'preview_id'), 'answer preview')
                    token = bounded(scalar(request.form, 'token'), 'preview version', 128)
                    result = policies.confirm(preview_id, token, current_user.email)
                    db.session.commit()
                    if result['status'] == 'deferred':
                        flash('Decision deferred. These purchases remain in suspense.', 'success')
                    elif result['status'] == 'saved_context_pending_review':
                        flash('Purchase context saved; individual accounting review is still needed. '
                              'No journal entries changed.', 'success')
                    else:
                        flash('Policy direction saved; accounting implementation and review are still needed. '
                              'No journal entries changed.', 'success')
                    return redirect(url_for('.amazon_policy_history', plan_id=result['plan_id']))
                if action != 'preview':
                    raise ValueError('Unknown policy question action.')
                selected_plan = identifier(scalar(request.form, 'plan_id'), 'suspense plan')
                selected_question = key(scalar(request.form, 'question_key'), 'question')
                form_values['choice'] = key(scalar(request.form, 'choice'), 'answer')
                form_values['scope'] = key(scalar(request.form, 'scope'), 'answer scope')
                if form_values['scope'] not in ('this_item', 'selected', 'future'):
                    raise ValueError('Choose this purchase, selected purchases, or future review guidance.')
                selected = request.form.getlist('item_id')
                if not selected or len(selected) > 100:
                    raise ValueError('Select between 1 and 100 purchases explicitly.')
                form_values['item_ids'] = [key(value, 'purchase') for value in selected]
                if len(set(form_values['item_ids'])) != len(selected):
                    raise ValueError('Select each purchase only once.')
                if form_values['scope'] == 'this_item' and len(selected) != 1:
                    raise ValueError('Select exactly one purchase for this purchase only.')
                form_values['custom_text'] = bounded(scalar(request.form, 'custom_text'), 'your explanation', 2000, False)
                form_values['threshold_amount'] = bounded(scalar(request.form, 'threshold_amount'), 'threshold amount', 24, False)
                form_values['threshold_basis'] = bounded(scalar(request.form, 'threshold_basis'), 'threshold basis', 32, False)
                token = bounded(scalar(request.form, 'token'), 'question version', 128)
                parameters = {}
                if form_values['choice'] == 'threshold':
                    parameters = {name: form_values[name] for name in ('threshold_amount', 'threshold_basis')}
                elif form_values['threshold_amount'] or form_values['threshold_basis']:
                    raise ValueError('Threshold details apply only to the threshold answer.')
                preview_id = policies.prepare_preview(
                    plan_id=selected_plan, question_key=selected_question, choice=form_values['choice'],
                    scope=form_values['scope'], item_ids=form_values['item_ids'],
                    custom_text=form_values['custom_text'], parameters=parameters,
                    expected_digest=token, reviewer=current_user.email)
                db.session.commit()
                return redirect(url_for('.amazon_policy_history', preview_id=preview_id))
            except (ValueError, KeyError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), 'error')
                if selected_plan and selected_question:
                    try:
                        return render_question(status=400)
                    except (ValueError, KeyError, TypeError):
                        db.session.rollback()
                return make_response(self.render('amazon_policy_questions.html', plan=None,
                    invalid_request=True, form_values=form_values), 400)

        try:
            preview_id = scalar(request.args, 'preview_id')
            if preview_id:
                context = policies.preview_page(identifier(preview_id, 'answer preview'), current_user.email)
                return make_response(self.render('amazon_policy_preview.html', **context))
            if scalar(request.args, 'plan_id'):
                selected_plan = identifier(scalar(request.args, 'plan_id'), 'suspense plan')
            if scalar(request.args, 'question_key'):
                selected_question = key(scalar(request.args, 'question_key'), 'question')
            return render_question()
        except (ValueError, KeyError, TypeError):
            db.session.rollback()
            abort(400)

    def _start_amazon_item_worker(self, run_id):
        root = Path(__file__).resolve().parents[2]
        subprocess.Popen([sys.executable, str(root / 'scripts/classify_amazon_items.py'), str(run_id)],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

    def _start_amazon_item_posting_worker(self, job_id):
        root = Path(__file__).resolve().parents[2]
        subprocess.Popen([sys.executable, str(root / 'scripts/post_amazon_items.py'), str(job_id)],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

    def _amazon_item_posting_status(self, run_id):
        from pacioli.functions import amazon_item_posting as item_posting
        status = item_posting.latest_status(run_id)
        if status is None:
            return None
        result = status.get('result') or {}
        return dict(id=status['id'], state=status['state'], progress=status['progress'],
                    revision=status.get('revision'), error=status.get('error'),
                    result={key: result[key] for key in (
                        'posted', 'revised', 'unchanged', 'held_settlements',
                        'classified_amount', 'suspense_amount') if key in result})

    @expose('/amazon/items/<int:run_id>/status/')
    def amazon_item_status(self, run_id):
        from pacioli.functions import amazon_item_workflow as item_workflow
        if not 0 < run_id <= 2147483647:
            abort(404)
        try:
            status = item_workflow.run_status(run_id)
            posting = self._amazon_item_posting_status(run_id)
        except (ValueError, KeyError, TypeError):
            abort(404)
        payload = {key: status[key] for key in ('id', 'state', 'progress', 'counts', 'revision')}
        payload['posting'] = posting
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @expose('/amazon/export/<export_name>/')
    def amazon_export(self,export_name):
        name=export_name
        if name=='all.zip':
            content=amazon_csv.export_zip();mime='application/zip';filename='pacioli-amazon-evidence.zip'
        elif name in ('receipts.csv','verified_items.csv','journal_allocations.csv'):
            content=amazon_csv.accounting_exports()[name];mime='text/csv; charset=utf-8';filename='pacioli-amazon-'+name
        else:abort(404)
        return self.amazon_download(content,mime,filename)

    @expose('/amazon/source/<int:identifier>/')
    def amazon_source(self,identifier):
        try:content,zipped=amazon_csv.original_archive(identifier)
        except ValueError:abort(404)
        suffix='zip' if zipped else 'csv'
        return self.amazon_download(content,'application/zip' if zipped else 'text/csv; charset=utf-8',
            'amazon-original-{}.{}'.format(identifier,suffix))

    @expose('/amazon/table/<int:identifier>/')
    def amazon_table(self,identifier):
        try:content=amazon_csv.source_table_csv(identifier)
        except ValueError:abort(404)
        return self.amazon_download(content,'text/csv; charset=utf-8','amazon-source-table-{}.csv'.format(identifier))

    def amazon_download(self,content,mime,filename):
        response=make_response(content)
        response.headers.update({'Content-Type':mime,'Cache-Control':'no-store',
            'Content-Disposition':'attachment; filename="{}"'.format(filename),
            'X-Content-Type-Options':'nosniff'})
        return response

    @expose('/amazon/status/')
    def amazon_status(self):
        run=db.session.query(AmazonCollectionRuns).order_by(AmazonCollectionRuns.id.desc()).first()
        response=jsonify(run=dict(id=run.id,state=run.state,progress=run.progress,documents=run.documents) if run else None)
        response.headers['Cache-Control']='no-store';return response

    @expose('/reviews/')
    def reviews(self):
        try:page=max(1,int(request.args.get('page',1)))
        except ValueError:abort(400)
        queue=db.session.execute(text('''SELECT m.id,m.keyword,count(j.id) AS entries,sum(j.functional_amount) AS amount
            FROM admin.mappings m JOIN bookkeeping.journal_entries j ON j.mapping_id=m.id
            WHERE m.source='ofx' AND m.match_mode='exact' AND m.review_required
            AND (j.debit_subaccount='Bookkeeping Suspense' OR j.credit_subaccount='Bookkeeping Suspense')
            GROUP BY m.id ORDER BY amount DESC,m.id LIMIT 25 OFFSET :offset'''),{'offset':(page-1)*25}).all()
        rows=db.session.query(AccountingReviews).order_by(AccountingReviews.id.desc()).limit(30).all()
        return self.page(tab='reviews',queue=queue,review_rows=rows,page=page)

    @expose('/review/new/',methods=('POST',))
    def review_new(self):
        require_action_csrf()
        try:
            row=reviews.create_review(int(request.form['mapping']),request.form.get('fact',''),request.form.get('source',''),current_user.email)
            db.session.commit()
            return redirect(url_for('.review',identifier=row.id))
        except (ValueError,KeyError) as exc:
            db.session.rollback();flash(str(exc),'error')
            return redirect(url_for('.reviews'))

    @expose('/review/<int:identifier>/',methods=('GET','POST'))
    def review(self,identifier):
        row=db.session.query(AccountingReviews).get(identifier)
        if not row:abort(404)
        if request.method=='POST':
            require_action_csrf()
            try:
                action=request.form['action']
                if action=='suggest':
                    # Serialize job creation across web requests; the GPU also has a host-wide lock.
                    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.review_worker'))"))
                    expired=datetime.now(timezone.utc)-timedelta(minutes=10)
                    for old in db.session.query(AccountingReviews).filter(AccountingReviews.state.in_(('queued','running')),AccountingReviews.updated_at<expired):
                        old.state='failed'
                    if db.session.query(AccountingReviews).filter(AccountingReviews.state.in_(('queued','running'))).first():
                        raise ValueError('A local review job is already running')
                    if row.state=='approved':raise ValueError('This review has been approved')
                    row.state='queued';row.updated_at=datetime.now(timezone.utc);db.session.commit()
                    root=Path(__file__).resolve().parents[2]
                    try:
                        subprocess.Popen([sys.executable,str(root/'scripts/workbench_advisor.py'),str(row.id)],
                            cwd=str(root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            start_new_session=True,close_fds=True)
                    except OSError:
                        row.state='failed';db.session.commit();raise ValueError('Could not start the local review worker')
                elif action=='approve':
                    reviews.approve_review(identifier,request.form['category'],request.form['reason'],current_user.email)
                    db.session.commit();flash('Exact mapping and all displayed linked journals updated.','success')
                else:raise ValueError('Unknown action')
            except (ValueError,KeyError) as exc:
                db.session.rollback();flash(str(exc),'error')
            return redirect(url_for('.review',identifier=identifier))
        snap=json.loads(row.snapshot)
        return self.page(tab='review',row=row,snapshot=snap,facts=json.loads(row.facts),
            proposal=json.loads(row.proposal) if row.proposal else None,
            categories=sorted(c['name'] for c in snap['chart']
                if (c['element']=='Expenses' and c['name'] not in ('Amazon Suspense Account','Discretionary Costs'))
                or c['name'] in ('Salary','Transfer Clearing')))

    @expose('/review/<int:identifier>/status/')
    def review_status(self,identifier):
        row=db.session.query(AccountingReviews).get(identifier)
        if not row:abort(404)
        response=jsonify(state=row.state,updated_at=row.updated_at.isoformat())
        response.headers['Cache-Control']='no-store'
        return response

    @expose('/evaluation/',methods=('GET','POST'))
    def evaluation(self):
        if request.method=='POST':
            require_action_csrf()
            try:
                reviews.add_gold_case(request.form['source_id'],request.form['merchant'],request.form['partition'],request.form['expected'],request.form['reason'],current_user.email)
                db.session.commit();flash('Independent gold case frozen.','success')
            except (ValueError,KeyError) as exc:
                db.session.rollback();flash(str(exc),'error')
        cases=db.session.query(AccountingEvaluationCases).order_by(AccountingEvaluationCases.id).all()
        runs=db.session.query(AccountingEvidence).filter_by(kind='real_evaluation').order_by(AccountingEvidence.id.desc()).limit(5).all()
        from scripts.evaluate_real_accounting import regression_passes
        results=[json.loads(r.payload) for r in runs]
        for result in results:result['regression_passed']=regression_passes(result)
        return self.page(tab='evaluation',cases=cases,runs=results)


admin.add_view(ReconciliationView(BankStatements,db.session,category='Accounting',name='Reconciliation',endpoint='reconciliation'))
