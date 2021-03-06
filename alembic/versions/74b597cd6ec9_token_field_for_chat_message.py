"""Token field for chat_message

Revision ID: 74b597cd6ec9
Revises: cdf4a9e99fd8
Create Date: 2017-10-13 17:14:38.807509

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '74b597cd6ec9'
down_revision = 'cdf4a9e99fd8'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('chat_messages', sa.Column('id_token', sa.String(), nullable=False))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('chat_messages', 'id_token')
    # ### end Alembic commands ###
