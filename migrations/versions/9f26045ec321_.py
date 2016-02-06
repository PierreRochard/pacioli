"""Adding unique constraint to journal entries

Revision ID: 9f26045ec321
Revises: None
Create Date: 2016-02-05 19:36:26.795720

"""

# revision identifiers, used by Alembic.
revision = '9f26045ec321'
down_revision = None

from alembic import op

def upgrade():
    op.create_unique_constraint(constraint_name='journal_entries_unique_constraint', table_name='journal_entries',
                                columns=['transaction_id', 'transaction_source'], schema='pacioli')


def downgrade():
    op.drop_constraint(constraint_name='journal_entries_unique_constraint', table_name='journal_entries',
                       schema='pacioli')