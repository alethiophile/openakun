import pytest
import time
import subprocess
import psycopg2
import secrets
import redis

from openakun import app, models, general, config, pages

from typing import Generator, AsyncGenerator

POSTGRES_IMAGE = "postgres:15-alpine"
CONTAINER_NAME = "pg-test"
DB_USER = "postgres"
DB_PASSWORD = "password"
DB_NAME = "postgres"
DB_PORT = 5678

REDIS_IMAGE = "redis:alpine"
REDIS_CONTAINER_NAME = "redis-test"
REDIS_PORT = 6379

@pytest.fixture(scope="session")
def redis_container() -> Generator[int, None, None]:
    """Fixture to run a temporary Redis container for testing."""

    # Start the Redis container
    subprocess.run([
        "docker", "run", "--rm", "-d",
        "--name", REDIS_CONTAINER_NAME,
        "-p", f"{REDIS_PORT}:6379",
        REDIS_IMAGE
    ], check=True)

    # Wait until Redis is ready
    client = redis.Redis(host="localhost", port=int(REDIS_PORT), decode_responses=True)
    for _ in range(30):  # Try for up to 30 seconds
        try:
            if client.ping():
                break
        except redis.ConnectionError:
            time.sleep(1)
    else:
        raise RuntimeError("Redis did not become ready in time")

    yield int(REDIS_PORT)  # Provide the Redis client for tests

    # Stop the Redis container after tests
    subprocess.run(["docker", "stop", REDIS_CONTAINER_NAME], check=True)

@pytest.fixture(scope="session")
def postgres_container() -> Generator[int, None, None]:
    """Fixture to run a temporary PostgreSQL container for testing."""

    # Start the PostgreSQL container
    subprocess.run([
        "docker", "run", "--rm", "-d",
        "--name", CONTAINER_NAME,
        "-e", f"POSTGRES_PASSWORD={DB_PASSWORD}",
        "-p", f"{DB_PORT}:5432",
        POSTGRES_IMAGE
    ], check=True)

    # Wait until PostgreSQL is ready
    while True:
        try:
            conn = psycopg2.connect(
                dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host="localhost", port=DB_PORT
            )
            conn.close()
            break
        except psycopg2.OperationalError:
            time.sleep(1)

    yield int(DB_PORT)  # Tests will now run using this database

    # Stop the container after tests
    subprocess.run(["docker", "stop", CONTAINER_NAME], check=True)

@pytest.fixture(scope="session")
async def openakun_app(
        postgres_container: int, redis_container: int
) -> AsyncGenerator[app.Quart, None]:
    cfg = config.Config(
        database_url=("postgresql://postgres:password@localhost"
                      f":{postgres_container}/postgres"),
        secret_key=secrets.token_urlsafe(),
        echo_sql=False,
        use_alembic=False,
        sentry_dsn=None,
        redis_url=f"redis://localhost:{redis_container}",
        csp_level=config.CSPLevel.Report,
        proxy_fix=False,
        main_origin="",
        merge_dict={}
    )

    await general.db_setup(cfg)
    await models.init_db(general.db.db_engine, use_alembic=False)

    oa = app.create_app(cfg)

    async with oa.app_context():
        await pages.add_user("admin", "", "password")
        await pages.add_user("user2", "", "password2")

    yield oa
