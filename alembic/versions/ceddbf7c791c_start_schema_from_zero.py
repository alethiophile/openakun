"""Start schema from zero

Revision ID: ceddbf7c791c
Revises: 
Create Date: 2017-09-23 14:30:10.279128

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ceddbf7c791c'
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
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('name', name=op.f('uq_users_name'))
    )
    op.create_table('stories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name=op.f('fk_stories_author_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stories'))
    )
    op.create_table('chapters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=True),
    sa.Column('story_id', sa.Integer(), nullable=False),
    sa.Column('is_appendix', sa.Boolean(), nullable=False),
    sa.Column('order_idx', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_chapters_story_id_stories')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chapters'))
    )
    op.create_table('posts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(), nullable=True),
    sa.Column('posted_date', sa.DateTime(), nullable=False),
    sa.Column('story_id', sa.Integer(), nullable=False),
    sa.Column('chapter_id', sa.Integer(), nullable=False),
    sa.Column('order_idx', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['chapter_id'], ['chapters.id'], name=op.f('fk_posts_chapter_id_chapters')),
    sa.ForeignKeyConstraint(['story_id'], ['stories.id'], name=op.f('fk_posts_story_id_stories')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_posts'))
    )


def downgrade():
    op.drop_table('posts')
    op.drop_table('chapters')
    op.drop_table('stories')
    op.drop_table('users')
