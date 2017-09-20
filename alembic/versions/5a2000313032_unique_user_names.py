"""Unique user names

Revision ID: 5a2000313032
Revises: 01dc05d8c691
Create Date: 2017-09-19 22:50:56.428248

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5a2000313032'
down_revision = '01dc05d8c691'
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(op.f('uq_users_name'), 'users', ['name'])


def downgrade():
    op.drop_constraint(op.f('uq_users_name'), 'users', type_='unique')
