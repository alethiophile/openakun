"""Added chat channel index

Revision ID: 72bfa8cd989c
Revises: edda1a2c4ff7
Create Date: 2017-10-14 14:23:31.847774

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '72bfa8cd989c'
down_revision = 'edda1a2c4ff7'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_index('channel_idx', 'chat_messages', ['channel_id', 'date'], unique=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('channel_idx', table_name='chat_messages')
    # ### end Alembic commands ###