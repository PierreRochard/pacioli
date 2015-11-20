#!/usr/bin/env python

import os

from flask.ext.script import Manager, Server
from flask.ext.script.commands import ShowUrls, Clean
from pacioli import create_app
from pacioli.models import db, User, Role

# default to dev config because no one should use this in
# production anyway
env = os.environ.get('pacioli_ENV', 'dev')
app = create_app('pacioli.settings.%sConfig' % env.capitalize(), env=env)

manager = Manager(app)
manager.add_command("server", Server())
manager.add_command("show-urls", ShowUrls())
manager.add_command("clean", Clean())


@manager.shell
def make_shell_context():
    """ Creates a python REPL with several default imports
        in the context of the app
    """

    return dict(app=app, db=db, User=User)


@manager.command
def createdb():
    """ Creates a database with all of the tables defined in
        your SQLAlchemy models
    """

    db.create_all()

@manager.command
def create_superuser():
    if User.query.count() == 1:
        if not Role.query.count():
            superuser = Role()
            superuser.name = 'superuser'
            superuser.description = 'superuser'
            db.session.add(superuser)
            db.session.commit()
        admin = User.query.first()
        admin.roles.append(superuser)
        db.session.commit()

if __name__ == "__main__":
    manager.run()
