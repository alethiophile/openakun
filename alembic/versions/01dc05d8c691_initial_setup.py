"""Initial setup

Revision ID: 01dc05d8c691
Revises: 
Create Date: 2017-09-18 22:35:49.036567

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '01dc05d8c691'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=True),
    sa.Column('email', sa.String(), nullable=True),
    sa.Column('password_hash', sa.String(), nullable=True),
    sa.Column('joined_date', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_table('stories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('author_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_stories_author_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stories'))
    )
    op.create_table('chapters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(), nullable=True),
    sa.Column('posted_date', sa.DateTime(), nullable=True),
    sa.Column('story_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_chapters_story_id_stories')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chapters'))
    )


def downgrade():
    op.drop_table('chapters')
    op.drop_table('stories')
    op.drop_table('users')
