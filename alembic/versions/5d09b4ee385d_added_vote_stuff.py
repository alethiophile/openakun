"""added vote stuff

Revision ID: 5d09b4ee385d
Revises: 7210849b51fe
Create Date: 2020-10-15 15:03:46.807067

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5d09b4ee385d'
down_revision = '7210849b51fe'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('vote_info', sa.Column('vote_question', sa.String(), nullable=False))
    op.create_unique_constraint(op.f('uq_vote_info_post_id'), 'vote_info', ['post_id'])
    op.drop_column('vote_info', 'active')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('vote_info', sa.Column('active', sa.BOOLEAN(), autoincrement=False, nullable=False))
    op.drop_constraint(op.f('uq_vote_info_post_id'), 'vote_info', type_='unique')
    op.drop_column('vote_info', 'vote_question')
    # ### end Alembic commands ###
