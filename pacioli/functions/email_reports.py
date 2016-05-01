from datetime import datetime, timedelta
import os

from flask import current_app
from flask.ext.mail import Message
from jinja2 import Template
from premailer import transform

from pacioli import db, mail
from pacioli.models import Transactions, AccountsFrom


def results_to_email_template(title, table_caption, table_header, query_results):
    templates_directory = os.path.abspath(__file__ + "/../../templates")
    assert os.path.exists(templates_directory), 'templates directory is missing'
    email_template = os.path.join(templates_directory, 'email_table_template.html')
    with open(email_template, 'r') as html_template:
        html_template_string = html_template.read()

    css_template = os.path.join(templates_directory, 'email_bootstrap.min.css')
    with open(css_template, 'r') as css:
        css_string = css.read()

    template = Template(html_template_string)

    html_body = template.render(title=title,
                                css=css_string,
                                table_caption=table_caption,
                                table_header=table_header,
                                table_rows=query_results).encode('utf-8')

    return transform(html_body).encode('utf-8')


def send_ofx_bank_transactions_report():
    start = datetime.now().date() - timedelta(days=1)
    new_transactions = (db.session.query(Transactions.id, Transactions.date, Transactions.amount,
                                         Transactions.description, Transactions.account)
                        .filter(Transactions.date > start)
                        .order_by(Transactions.date.desc()).all())
    if new_transactions:
        header = ['ID', 'Date', 'Amount', 'Description', 'Account']
        transactions = [[cell for cell in row] for row in new_transactions]
        for row in transactions:
            row[0] = '...' + str(row[0])[-4:-1]
            row[1] = row[1].date()
            row[2] = '{0:,.2f}'.format(row[2])
        html_body = results_to_email_template('New Transactions', '', header, transactions)
        msg = Message('New Transactions', recipients=[current_app.config['MAIL_USERNAME']], html=html_body)
        mail.send(msg)
