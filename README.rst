This is openakun, a (very-)work-in-progress open-source clone of the
anonkun/fiction.live real-time interactive questing engine.

Currently it's planned to use flask, sqlalchemy for database, flask-socketio for
real-time, alembic for migrations.

Database entities: user, story, chapter?, post, chat message. Posts can be
votes, not sure whether to break that out in the database or just store current
values. Topics should also be handled somehow.

openakun is now testable, though still lacking any of the functionality of an
actual site. Steps to run:

1. Check out the repository.
2. Create a new virtualenv. I like to use `pew
   <https://github.com/berdario/pew>`_ for this.
3. Install the package and dependencies: ``pip3 install -e .``
4. Set up a database backend. I use a local PostgreSQL server, but anything
   supported by sqlalchemy will do. sqlite is currently not supported due to
   limitations in alembic.
5. Once the database is configured, set the url in ``alembic.ini`` and
   ``models.py``, then run ``alembic upgrade head`` to create the schema.
6. Run the development server: ``FLASK_APP=openakun.pages flask run``
