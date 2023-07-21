This is openakun, a (very-)work-in-progress open-source clone of the
anonkun/fiction.live real-time interactive questing engine.

openakun is now testable, though development is very incomplete. Steps to run:

1. Check out the repository.
2. Install Poetry, if not present already. Enter the project virtualenv:
   ``poetry shell``
3. Install the package and dependencies: ``poetry install``
4. Set up a database backend. PostgreSQL is currently the only supported
   database backend. Ensure that a database and user have been created and you
   can connect in TCP mode.
5. Set up Redis.
6. Create the openakun configuration by copying the openakun.cfg.default file to
   openakun.cfg, then set all the required values. In particular, you must set
   ``database_url`` and ``secret_key``. If you want to use anything other than
   the default Redis, you must also set ``redis_url``.
7. Initialize the database: ``openakun_initdb``
8. Run the development server: ``openakun_server``
9. Run the Celery worker: ``celery -A openakun.tasks worker --loglevel=INFO -B``

Using Docker for development:

openakun now includes Docker config for development. To run using Docker:

1. Build the image: ``docker compose -f docker_compose.dev.yml build``
2. Ensure that your ``openakun.cfg`` file in the dev directory refers to the
   proper hosts. The Postgres URL should be
   ``postgresql://postgres:password@postgres/postgres``. The Redis URL should be
   ``redis://redis``.
3. Run the image: ``docker compose -f docker_compose.dev.yml up``

This exposes the app on port 4430. Currently the Docker configuration is only
suitable for development.
