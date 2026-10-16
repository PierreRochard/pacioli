"""Alembic environment for Pacioli's schema; see migrations/README."""

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

from pacioli.database.migration_scope import CONFIGURE_ARGS


config = context.config
# Keep the loggers that the app or the tests configured before migrations ran.
fileConfig(config.config_file_name, disable_existing_loggers=False)
logger = logging.getLogger('alembic.env')
target_db = current_app.extensions['migrate'].db
config.set_main_option(
    'sqlalchemy.url',
    target_db.engine.url.render_as_string(hide_password=False).replace('%', '%%'))


def run_migrations_offline():
    """Emit the migration SQL instead of running it."""
    context.configure(url=config.get_main_option('sqlalchemy.url'),
                      target_metadata=target_db.metadata, literal_binds=True,
                      **CONFIGURE_ARGS)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    def process_revision_directives(context, revision, directives):
        # Do not write an empty revision when autogenerate finds no change.
        if getattr(config.cmd_opts, 'autogenerate', False):
            if directives[0].upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    arguments = dict(CONFIGURE_ARGS, **current_app.extensions['migrate'].configure_args)
    arguments.setdefault('process_revision_directives', process_revision_directives)
    with target_db.engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_db.metadata,
                          **arguments)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
