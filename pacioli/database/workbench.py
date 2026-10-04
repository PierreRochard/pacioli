"""Additive, repeatable migration; historical journal rows are untouched."""
from sqlalchemy import text
from pacioli.extensions import db


def install_amazon_item_posting(engine):
    """Install only the additive posting queue and its authorization guard."""
    from pacioli.models import AmazonItemPostRuns
    with engine.begin() as connection:
        AmazonItemPostRuns.__table__.create(connection, checkfirst=True)
        connection.execute(text('''CREATE OR REPLACE FUNCTION amazon.reject_item_post_request_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.request_key IS DISTINCT FROM OLD.request_key OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Posting authorization is immutable; create a new reviewed request';
            END IF;
            RETURN NEW;
            END $$'''))
        connection.execute(text('DROP TRIGGER IF EXISTS immutable_item_post_request ON amazon.item_post_runs'))
        connection.execute(text('''CREATE TRIGGER immutable_item_post_request
            BEFORE UPDATE ON amazon.item_post_runs FOR EACH ROW
            EXECUTE FUNCTION amazon.reject_item_post_request_mutation()'''))


def install_amazon_suspense(engine):
    """Add the encrypted suspense-review workbench without changing journals."""
    from pacioli.models import (AmazonSuspensePlans, AmazonSuspenseItems,
                               AmazonSuspenseFacts, AmazonProductRules)
    with engine.begin() as connection:
        for model in (AmazonSuspensePlans, AmazonSuspenseItems, AmazonSuspenseFacts, AmazonProductRules):
            model.__table__.create(connection, checkfirst=True)
        connection.execute(text('''CREATE OR REPLACE FUNCTION amazon.reject_suspense_evidence_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Suspense review evidence is immutable; append a new reviewed event';
            END $$'''))
        for table in ('suspense_items', 'suspense_facts', 'product_rules'):
            connection.execute(text('DROP TRIGGER IF EXISTS immutable_suspense_evidence ON amazon.' + table))
            connection.execute(text('CREATE TRIGGER immutable_suspense_evidence BEFORE UPDATE OR DELETE ON amazon.' + table +
                ' FOR EACH ROW EXECUTE FUNCTION amazon.reject_suspense_evidence_mutation()'))
        connection.execute(text('''CREATE OR REPLACE FUNCTION amazon.reject_suspense_plan_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Suspense plans are immutable'; END IF;
            IF NEW.id IS DISTINCT FROM OLD.id OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
                OR NEW.document_digest IS DISTINCT FROM OLD.document_digest
                OR NEW.source_run_id IS DISTINCT FROM OLD.source_run_id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Suspense plan source evidence is immutable';
            END IF;
            RETURN NEW;
            END $$'''))
        connection.execute(text('DROP TRIGGER IF EXISTS immutable_suspense_plan ON amazon.suspense_plans'))
        connection.execute(text('''CREATE TRIGGER immutable_suspense_plan BEFORE UPDATE OR DELETE
            ON amazon.suspense_plans FOR EACH ROW EXECUTE FUNCTION amazon.reject_suspense_plan_mutation()'''))


def install_amazon_policy_questions(engine):
    """Add owner decision collection without altering financial eligibility."""
    from pacioli.models import AmazonPolicyAnswers
    with engine.begin() as connection:
        AmazonPolicyAnswers.__table__.create(connection, checkfirst=True)
        connection.execute(text('''CREATE OR REPLACE FUNCTION amazon.reject_policy_answer_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Policy answers are immutable; save a new answer';
            END $$'''))
        connection.execute(text('DROP TRIGGER IF EXISTS immutable_policy_answer ON amazon.policy_answers'))
        connection.execute(text('''CREATE TRIGGER immutable_policy_answer BEFORE UPDATE OR DELETE
            ON amazon.policy_answers FOR EACH ROW EXECUTE FUNCTION amazon.reject_policy_answer_mutation()'''))


def install_workbench():
    from pacioli.models import (AccountingEvidence, BankStatements, OpeningBalances,
        TransferMatches, TransferMatchLegs, AmazonEvidenceBundles, AccountingReviews,
        AccountingEvaluationCases, AmazonCollectionRuns, AmazonItemRuns, AmazonItemJobs)
    for model in (AccountingEvidence,BankStatements,OpeningBalances,TransferMatches,
                  TransferMatchLegs,AmazonEvidenceBundles,AccountingReviews,AccountingEvaluationCases,AmazonCollectionRuns,
                  AmazonItemRuns,AmazonItemJobs):
        model.__table__.create(db.engine,checkfirst=True)
    install_amazon_item_posting(db.engine)
    install_amazon_suspense(db.engine)
    install_amazon_policy_questions(db.engine)
    with db.engine.begin() as connection:
        connection.execute(text('''CREATE OR REPLACE FUNCTION bookkeeping.reject_evidence_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Accounting evidence is immutable; record a new reviewed event'; END $$'''))
        for table in ('accounting_evidence','bank_statements','opening_balances',
                      'transfer_matches','transfer_match_legs','accounting_evaluation_cases'):
            connection.execute(text('DROP TRIGGER IF EXISTS immutable_evidence ON bookkeeping.'+table))
            connection.execute(text('CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON bookkeeping.'+table+
                ' FOR EACH ROW EXECUTE FUNCTION bookkeeping.reject_evidence_mutation()'))
