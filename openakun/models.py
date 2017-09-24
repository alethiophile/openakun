#!/usr/bin/env python3
# despite not a script...

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import (Column, Integer, String, ForeignKey, DateTime,
                        MetaData, Boolean)
from sqlalchemy import create_engine  # noqa: F401
from sqlalchemy.orm import relationship, sessionmaker  # noqa: F401

import os

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

# This satisfies the requirements of flask_login for a User class.
class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    email = Column(String)
    password_hash = Column(String)
    joined_date = Column(DateTime)

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

class Story(Base):
    __tablename__ = 'stories'

    id = Column(Integer, primary_key=True)
    title = Column(String)
    description = Column(String)
    author_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    author = relationship("User", backref="stories")

class Chapter(Base):
    __tablename__ = 'chapters'

    id = Column(Integer, primary_key=True)
    title = Column(String)
    story_id = Column(Integer, ForeignKey('stories.id'), nullable=False)
    is_appendix = Column(Boolean, nullable=False, default=False)
    order_idx = Column(Integer, nullable=False)

    story = relationship("Story", backref="chapters")

class Post(Base):
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    text = Column(String)
    posted_date = Column(DateTime, nullable=False)
    story_id = Column(Integer, ForeignKey('stories.id'), nullable=False)
    chapter_id = Column(Integer, ForeignKey('chapters.id'), nullable=False)
    order_idx = Column(Integer, nullable=False)

    story = relationship("Story", backref="posts")
    chapter = relationship("Chapter", backref="posts")

def init_db(engine):
    Base.metadata.create_all(engine)

    from alembic.config import Config
    from alembic import command
    alembic_cfg = Config("alembic.ini")
    command.stamp(alembic_cfg, "head")
