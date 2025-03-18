#!/usr/bin/env python3
# despite not a script...

from __future__ import annotations

from sqlalchemy import (Column, Integer, ForeignKey, DateTime, MetaData,
                        CheckConstraint, UniqueConstraint, Index, Table)
from sqlalchemy import func
from sqlalchemy.orm import (relationship, DeclarativeBase, Mapped,
                            mapped_column)
from sqlalchemy.sql import select
from sqlalchemy.ext.asyncio import \
    (AsyncAttrs, async_sessionmaker, AsyncSession, create_async_engine,
     AsyncEngine)
from datetime import datetime

import os, enum

from typing import Any

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
    md = MetaData()
else:
    md = MetaData(naming_convention=naming)

class Base(AsyncAttrs, DeclarativeBase):
    metadata = md

user_with_role = Table(
    'user_with_role', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('role_id', Integer, ForeignKey('user_roles.id'), primary_key=True))

story_with_tag = Table(
    'story_tags', Base.metadata,
    Column('story_id', Integer, ForeignKey('stories.id'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tags.id'), primary_key=True))

# This satisfies the requirements of flask_login for a User class.
class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str | None]
    email_verified: Mapped[bool] = mapped_column(default=False)
    password_hash: Mapped[str]
    joined_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[UserRole]] = relationship(secondary=user_with_role)
    stories: Mapped[list[Story]] = relationship(back_populates="author")

    def __repr__(self) -> str:
        return "<User '{}' (id {})>".format(self.name, self.id)

    # Methods for flask_login
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

class UserRole(Base):
    __tablename__ = 'user_roles'

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]

class Story(Base):
    __tablename__ = 'stories'

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    description: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    channel_id: Mapped[int] = mapped_column(ForeignKey('channels.id'))

    author: Mapped[User] = relationship(back_populates="stories")
    channel: Mapped[Channel] = relationship(uselist=False)
    posts: Mapped[list[Post]] = relationship(back_populates="story",
                                             order_by='Post.order_idx')
    topics: Mapped[list[Topic]] = relationship(back_populates='story')

    chapters: Mapped[list[Chapter]] = relationship(
        back_populates='story',
        order_by='Chapter.is_appendix,Chapter.order_idx',
        uselist=True)

    tags: Mapped[list[Tag]] = relationship(back_populates='stories',
                                           secondary=story_with_tag)

    def __repr__(self) -> str:
        return "<Story '{}' (id {}) by {}>".format(self.title, self.id,
                                                   self.author.name)

class Chapter(Base):
    __tablename__ = 'chapters'

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    story_id: Mapped[int] = mapped_column(ForeignKey('stories.id'))
    is_appendix: Mapped[bool] = mapped_column(default=False)
    order_idx: Mapped[int]

    story: Mapped[Story] = relationship(back_populates='chapters')
    posts: Mapped[list[Post]] = relationship(back_populates='chapter')

    def __repr__(self) -> str:
        return ("<Chapter '{}' (id {}, idx {}) of '{}', appendix={}>".
                format(self.title, self.id, self.order_idx, self.story.title,
                       self.is_appendix))

class PostType(enum.Enum):
    Text = 1
    Vote = 2
    Writein = 3

def order_idx_default(context: Any) -> int:
    cid = context.get_current_parameters()['chapter_id']
    rv = context.connection.execute(select(
        func.coalesce(
            func.max(Post.order_idx) + 10,
            0)).where(Post.chapter_id == cid))
    rows = rv.fetchall()
    return rows[0][0]

class Post(Base):
    __tablename__ = 'posts'

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str | None]
    posted_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    story_id: Mapped[int] = mapped_column(ForeignKey('stories.id'))
    chapter_id: Mapped[int] = mapped_column(ForeignKey('chapters.id'))
    order_idx: Mapped[int] = mapped_column(default=order_idx_default)
    post_type: Mapped[PostType] = mapped_column(default=PostType.Text)
    # null unless type is Vote
    # vote_id = Column(Integer, ForeignKey('vote_info.id'))

    story: Mapped[Story] = relationship(back_populates="posts")
    chapter: Mapped[Chapter] = relationship(back_populates="posts")
    vote_info: Mapped[VoteInfo] = relationship(uselist=False,
                                               back_populates="post")

class VoteInfo(Base):
    __tablename__ = 'vote_info'

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique is True to enforce one-to-one relationship with Post
    post_id: Mapped[int] = mapped_column(ForeignKey('posts.id'),
                                         unique=True)
    vote_question: Mapped[str]
    multivote: Mapped[bool] = mapped_column(default=True)
    writein_allowed: Mapped[bool] = mapped_column(default=True)
    votes_hidden: Mapped[bool] = mapped_column(default=False)
    time_closed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    votes: Mapped[list[VoteEntry]] = relationship(uselist=True,
                                                  back_populates='vote_info')
    post: Mapped[Post] = relationship(back_populates='vote_info')

    def __repr__(self) -> str:
        return (f"VoteInfo(id={self.id}, post_id={self.post_id}, "
                f"vote_question=\"{self.vote_question}\", "
                f"multivote={self.multivote}, "
                f"writein_allowed={self.writein_allowed}, "
                f"votes_hidden={self.votes_hidden}, "
                f"time_closed={self.time_closed}, votes={self.votes})")

class VoteEntry(Base):
    __tablename__ = 'vote_entries'

    id: Mapped[int] = mapped_column(primary_key=True)
    vote_id: Mapped[int] = mapped_column(ForeignKey('vote_info.id'))
    vote_text: Mapped[str]
    killed: Mapped[bool] = mapped_column(default=False)
    killed_text: Mapped[str | None]

    votes: Mapped[list[UserVote]] = relationship(uselist=True,
                                                 cascade='all, delete-orphan')
    vote_info: Mapped[VoteInfo] = relationship(uselist=False,
                                               back_populates='votes')

    def __repr__(self) -> str:
        return (f"VoteEntry(id={self.id}, vote_id={self.vote_id}, "
                f"vote_text={self.vote_text}, killed={self.killed}, "
                f"killed_text={self.killed_text}, votes={self.votes})")

class UserVote(Base):
    __tablename__ = 'user_votes'
    __table_args__ = (
        CheckConstraint('(user_id is null) != (anon_id is null)',
                        name='user_or_anon'),
        UniqueConstraint('entry_id', 'user_id', 'anon_id'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey('vote_entries.id'))
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    anon_id: Mapped[str | None]

    def __repr__(self) -> str:
        return (f'UserVote(entry_id={self.entry_id}, ' +
                (f'user_id={self.user_id}' if self.user_id is not None else
                 f'anon_id={self.anon_id}') + ')')

class WriteinEntry(Base):
    __tablename__ = 'writein_entries'
    __table_args__ = (
        CheckConstraint('(user_id is null) != (anon_id is null)',
                        name='user_or_anon'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    anon_id: Mapped[str | None]
    text: Mapped[str]
    date_added: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class Channel(Base):
    __tablename__ = 'channels'

    id: Mapped[int] = mapped_column(primary_key=True)
    private: Mapped[bool] = mapped_column(default=False)

    story: Mapped[Story] = relationship(back_populates='channel')
    messages: Mapped[list[ChatMessage]] = relationship(back_populates='channel')

class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        CheckConstraint('(user_id is null) != (anon_id is null)',
                        name='user_or_anon'),
        Index('channel_idx', 'channel_id', 'date')
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    id_token: Mapped[str] = mapped_column(unique=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey('channels.id'))
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    anon_id: Mapped[str | None]
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str]
    special: Mapped[bool] = mapped_column(default=False)
    image: Mapped[bool] = mapped_column(default=False)

    user: Mapped[User] = relationship()
    channel: Mapped[Channel] = relationship(back_populates="messages")

class AddressIdentifier(Base):
    __tablename__ = 'address_identifier'

    hash: Mapped[str] = mapped_column(primary_key=True)
    ip: Mapped[str]

class Topic(Base):
    __tablename__ = 'topics'

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    poster_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    post_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # if null, it's a "front page" topic accessible from the homepage
    story_id: Mapped[int | None] = mapped_column(ForeignKey('stories.id'))

    story: Mapped[Story | None] = relationship(back_populates="topics")
    poster: Mapped[User] = relationship()

    messages: Mapped[list[TopicMessage]] = relationship(
        back_populates="topic",
        order_by="TopicMessage.post_date")

class TopicMessage(Base):
    __tablename__ = 'topic_messages'

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'))
    poster_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    post_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str]

    topic: Mapped[Topic] = relationship(back_populates="messages")
    poster: Mapped[User] = relationship()

class Tag(Base):
    __tablename__ = 'tags'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    canonical_tag: Mapped[int | None] = mapped_column(ForeignKey('tags.id'))

    stories: Mapped[list[Story]] = relationship(back_populates='tags',
                                                secondary=story_with_tag)

async def ensure_updated_db() -> None:
    from alembic.config import Config
    from alembic import command
    

async def init_db(engine: AsyncEngine, use_alembic: bool = True) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # the Alembic operations are sync, so theoretically bad to run from async
    # code; this should be fine as long as it's contained to the setup phases
    # only
    if use_alembic:
        from alembic.config import Config
        from alembic import command
        alembic_cfg = Config("alembic.ini")
        command.stamp(alembic_cfg, "head")
