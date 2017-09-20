#!/usr/bin/env python3
# despite not a script...

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, MetaData
from sqlalchemy import create_engine
from sqlalchemy.orm import relationship, sessionmaker

# for Alembic
naming = {
    "ix": 'ix_%(column_0_label)s',
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=naming))

engine = create_engine("postgresql://devel@localhost/openakun")  # temporary
Session = sessionmaker(bind=engine)

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
    author_id = Column(Integer, ForeignKey('users.id'))

    author = relationship("User", backref="stories")

class Chapter(Base):
    __tablename__ = 'chapters'

    id = Column(Integer, primary_key=True)
    text = Column(String)
    posted_date = Column(DateTime)
    story_id = Column(Integer, ForeignKey('stories.id'))

    story = relationship("Story", backref="chapters")
