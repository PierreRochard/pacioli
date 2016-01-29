from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, COMMASPACE
import os
import smtplib

from ofx_config import MAIL_USERNAME, MAIL_PASSWORD, SMTP_SERVER, SMTP_PORT


def send_email(recipients, subject, text_body='', html_body='', cc=(), attachments=()):
    message = MIMEMultipart()
    message['From'] = MAIL_USERNAME
    assert isinstance(recipients, list)
    message['To'] = COMMASPACE.join(recipients)
    if cc:
        message['Cc'] = COMMASPACE.join(cc)
        recipients += cc
    message['Date'] = formatdate(localtime=True)
    assert isinstance(subject, str)
    message['Subject'] = subject
    if text_body:
        assert isinstance(text_body, str)
        message.attach(MIMEText(text_body, 'plain'))
    if html_body:
        assert isinstance(html_body, str)
        message.attach(MIMEText(html_body, 'html'))
    for attachment in attachments:
        with open(attachment, 'rb') as attached_file:
            attachment_name = os.path.basename(attachment)
            content_disposition = 'attachment; filename="{0}"'.format(attachment_name)
            mime = MIMEApplication(attached_file.read(),
                                   Content_Disposition=content_disposition,
                                   Name=attachment_name)
            message.attach(mime)
    server = smtplib.SMTP(host=SMTP_SERVER, port=SMTP_PORT)
    server.ehlo()
    server.starttls()
    server.login(MAIL_USERNAME, MAIL_PASSWORD)
    server.sendmail(MAIL_USERNAME, recipients, message.as_string())
    server.quit()