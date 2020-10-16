#!/usr/bin/env python3
# despite not a script...

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import (Column, Integer, String, ForeignKey, DateTime,
                        MetaData, Boolean, CheckConstraint, Index, Table, Enum)
from sqlalchemy import create_engine, func  # noqa: F401
from sqlalchemy.orm import relationship, sessionmaker, backref  # noqa: F401

import os, enum

# for Alembic
naming = {
    "ix": 'ix_%(column_0_label)s',
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Disable naming convention if we're in the test suite; this allows doing it in
# sqlite to work
if os.environ.get('OPENAKUN_TESTING') == '1':
    Base = declarative_base()
else:
    Base = declarative_base(metadata=MetaData(naming_convention=naming))

user_with_role = Table('user_with_role', Base.metadata,
                       Column('user_id', Integer, ForeignKey('users.id')),
                       Column('role_id', Integer, ForeignKey('user_roles.id')))

# This satisfies the requirements of flask_login for a User class.
class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    email = Column(String)
    email_verified = Column(Boolean, nullable=False, default=False)
    password_hash = Column(String)
    joined_date = Column(DateTime(timezone=True))

    roles = relationship("UserRole", secondary=user_with_role,
                         backref='users')

    def __repr__(self):
        return "<User '{}' (id {})>".format(self.name, self.id)

    # Methods for flask_login
    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

class UserRole(Base):
    __tablename__ = 'user_roles'

    id = Column(Integer, primary_key=True)
    title = Column(String)

class Story(Base):
    __tablename__ = 'stories'

    id = Column(Integer, primary_key=True)
    title = Column(String)
    description = Column(String)
    author_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    channel_id = Column(Integer, ForeignKey('channels.id'), nullable=False)

    author = relationship("User", backref="stories")
    channel = relationship("Channel", uselist=False)

    def __repr__(self):
        return "<Story '{}' (id {}) by {}>".format(self.title, self.id,
                                                   self.author.name)

class Chapter(Base):
    __tablename__ = 'chapters'

    id = Column(Integer, primary_key=True)
    title = Column(String)
    story_id = Column(Integer, ForeignKey('stories.id'), nullable=False)
    is_appendix = Column(Boolean, nullable=False, default=False)
    order_idx = Column(Integer, nullable=False)

    story = relationship("Story", backref=backref(
        "chapters",
        order_by='Chapter.is_appendix,Chapter.order_idx'
    ))

    def __repr__(self):
        return ("<Chapter '{}' (id {}, idx {}) of '{}', appendix={}>".
                format(self.title, self.id, self.order_idx, self.story.title,
                       self.is_appendix))

class PostType(enum.Enum):
    Text = 1
    Vote = 2
    Writein = 3

class Post(Base):
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    text = Column(String)
    posted_date = Column(DateTime(timezone=True), nullable=False)
    story_id = Column(Integer, ForeignKey('stories.id'), nullable=False)
    chapter_id = Column(Integer, ForeignKey('chapters.id'), nullable=False)
    order_idx = Column(Integer, nullable=False)
    post_type = Column(Enum(PostType), default=PostType.Text, nullable=False)
    # null unless type is Vote
    # vote_id = Column(Integer, ForeignKey('vote_info.id'))

    story = relationship("Story", backref=backref(
        "posts",
        order_by='Post.order_idx'
    ))
    chapter = relationship("Chapter", backref="posts")
    vote_info = relationship("VoteInfo", uselist=False, back_populates="post")

class VoteInfo(Base):
    __tablename__ = 'vote_info'

    id = Column(Integer, primary_key=True)
    # unique is True to enforce one-to-one relationship with Post
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False,
                     unique=True)
    vote_question = Column(String, nullable=False)
    multivote = Column(Boolean, default=True, nullable=False)
    writein_allowed = Column(Boolean, default=True, nullable=False)
    votes_hidden = Column(Boolean, default=False, nullable=False)
    time_closed = Column(DateTime(timezone=True))

    votes = relationship('VoteEntry', uselist=True, back_populates='vote_info')
    post = relationship('Post', back_populates='vote_info')

class VoteEntry(Base):
    __tablename__ = 'vote_entries'

    id = Column(Integer, primary_key=True)
    vote_id = Column(Integer, ForeignKey('vote_info.id'), nullable=False)
    vote_text = Column(String, nullable=False)
    killed = Column(Boolean, default=False, nullable=False)
    killed_text = Column(String)

    votes = relationship('UserVote')
    vote_info = relationship('VoteInfo', uselist=False, back_populates='votes')

class UserVote(Base):
    __tablename__ = 'user_votes'
    __table_args__ = (
        CheckConstraint('(user_id is null) != (anon_id is null)',
                        name='user_or_anon'),
    )

    id = Column(Integer, primary_key=True)
    entry_id = Column(Integer, ForeignKey('vote_entries.id'), nullable=False,
                      )
    user_id = Column(Integer, ForeignKey('users.id'),
                     nullable=True)
    anon_id = Column(String, nullable=True)

class WriteinEntry(Base):
    __tablename__ = 'writein_entries'
    __table_args__ = (
        CheckConstraint('(user_id is null) != (anon_id is null)',
                        name='user_or_anon'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    anon_id = Column(String)
    text = Column(String)
    date_added = Column(DateTime(timezone=True), nullable=False)

class Channel(Base):
    __tablename__ = 'channels'

    id = Column(Integer, primary_key=True)
    private = Column(Boolean, default=False, nullable=False)

    story = relationship("Story", back_populates='channel')

class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        CheckConstraint('(user_id is null) != (anon_id is null)',
                        name='user_or_anon'),
        Index('channel_idx', 'channel_id', 'date')
    )

    id = Column(Integer, primary_key=True)
    id_token = Column(String, nullable=False, unique=True)
    channel_id = Column(Integer, ForeignKey('channels.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    anon_id = Column(String)
    date = Column(DateTime(timezone=True), nullable=False)
    text = Column(String)
    special = Column(Boolean, default=False, nullable=False)
    image = Column(Boolean, default=False, nullable=False)

    user = relationship("User")
    channel = relationship("Channel", backref="messages")

class AddressIdentifier(Base):
    __tablename__ = 'address_identifier'

    hash = Column(String, primary_key=True)
    ip = Column(String, nullable=False)

def init_db(engine, use_alembic=True):
    Base.metadata.create_all(engine)

    if use_alembic:
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config("alembic.ini")
        command.stamp(alembic_cfg, "head")
