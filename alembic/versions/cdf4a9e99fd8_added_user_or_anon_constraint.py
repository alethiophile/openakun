"""Added user_or_anon constraint

Revision ID: cdf4a9e99fd8
Revises: 94b5cb01d6a5
Create Date: 2017-10-13 14:25:58.886247

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cdf4a9e99fd8'
down_revision = '94b5cb01d6a5'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_check_constraint('user_or_anon', 'chat_messages',
                               "(user_id is null) != (anon_id is null)")
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('ck_chat_messages_user_or_anon', 'chat_messages')
    # ### end Alembic commands ###
