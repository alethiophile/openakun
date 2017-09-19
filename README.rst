This is openakun, a (very-)work-in-progress open-source clone of the
anonkun/fiction.live real-time interactive questing engine.

Currently it's planned to use flask, sqlalchemy for database, flask-socketio for
real-time, alembic for migrations.

Database entities: user, story, chapter?, post, chat message. Posts can be
votes, not sure whether to break that out in the database or just store current
values. Topics should also be handled somehow.
