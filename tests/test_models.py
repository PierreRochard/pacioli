import csv
from pathlib import Path

from flask_security.utils import verify_password

from pacioli.extensions import db
from pacioli.models import Subaccounts, Users


def test_bundled_chart_has_unambiguous_hierarchy():
    chart = Path(__file__).resolve().parents[1] / 'Generic Chart of Accounts.csv'
    with chart.open(newline='', encoding='utf-8') as source:
        rows = list(csv.DictReader(source))
    for child, parent in (('Classification', 'Element'),
                          ('Account', 'Classification'),
                          ('Subaccount', 'Account')):
        parents_by_name = {}
        for row in rows:
            name = row[child]
            if name in parents_by_name:
                assert parents_by_name[name] == row[parent], (child, name)
            else:
                parents_by_name[name] = row[parent]
    assert {row['Element'] for row in rows} == {
        'Assets', 'Liabilities', 'Capital Contributions', 'Capital Distributions',
        'Revenues', 'Expenses', 'Gains', 'Losses'}


def test_chart_of_accounts_is_populated_once(app_context):
    from pacioli.database.setup import populate_chart

    initial_count = db.session.query(Subaccounts).count()
    populate_chart()

    assert initial_count >= 45
    assert db.session.query(Subaccounts).count() == initial_count
    assert db.session.query(Subaccounts).filter_by(name='Accounts Payable').one()


def test_user_password_is_hashed_and_persisted(app_context, admin_user):
    stored = db.session.query(Users).filter_by(email=admin_user['email']).one()

    assert stored.password != 'test-password'
    assert verify_password(admin_user['password'], stored.password)
