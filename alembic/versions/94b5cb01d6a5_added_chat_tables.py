"""Added chat tables

Revision ID: 94b5cb01d6a5
Revises: 40f60062ddd5
Create Date: 2017-10-12 22:48:04.053372

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '94b5cb01d6a5'
down_revision = '40f60062ddd5'
branch_labels = None
depends_on = None

Base = sa.ext.declarative.declarative_base()
Session = sa.orm.sessionmaker()

class Story(Base):
    __tablename__ = 'stories'

    id = sa.Column(sa.Integer, primary_key=True)
    channel_id = sa.Column(sa.Integer, sa.ForeignKey('channels.id'), nullable=False)
    channel = sa.orm.relationship('Channel', backref='story')

class Channel(Base):
    __tablename__ = 'channels'

    id = sa.Column(sa.Integer, primary_key=True)
    private = sa.Column(sa.Boolean, default=False, nullable=False)

def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    conn = op.get_bind()
    s = Session(bind=conn)
    op.create_table('channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('private', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_channels'))
    )
    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('anon_id', sa.String(), nullable=True),
    sa.Column('date', sa.DateTime(timezone=True), nullable=False),
    sa.Column('text', sa.String(), nullable=True),
    sa.Column('special', sa.Boolean(), nullable=False),
    sa.Column('image', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], name=op.f('fk_chat_messages_channel_id_channels')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_chat_messages_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages'))
    )
    op.add_column('stories', sa.Column('channel_id', sa.Integer()))
    op.create_foreign_key(op.f('fk_stories_channel_id_channels'), 'stories', 'channels', ['channel_id'], ['id'])
    for i in s.query(Story):
        c = Channel()
        s.add(c)
        i.channel = c
    s.commit()
    op.alter_column('stories', 'channel_id', nullable=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(op.f('fk_stories_channel_id_channels'), 'stories', type_='foreignkey')
    op.drop_column('stories', 'channel_id')
    op.drop_table('chat_messages')
    op.drop_table('channels')
    # ### end Alembic commands ###
