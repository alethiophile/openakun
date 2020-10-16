#!python3

from .pages import config, parse_redis_url
from .models import Base
from .realtime import message_cache_len, ChatMessage

import celery, redis, json
from celery.signals import worker_process_init, worker_process_shutdown

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.dialects import postgresql

from datetime import datetime, timezone, timedelta

from typing import Dict, Any, List

redis_url = config['openakun']['redis_url']
queue = celery.Celery()
queue.conf.update(
    broker_url=redis_url,
    result_backend=redis_url,
)

@queue.on_after_configure.connect
def setup_periodic(sender, **kwargs):
    sender.add_periodic_task(60.0, save_chat_messages.s())

db_engine = None
db_session = None
redis_conn = None

@worker_process_init.connect
def get_db_conn(**kwargs):
    global db_engine, db_session, redis_conn
    db_engine = create_engine(config['openakun']['database_url'], echo=True)
    db_session = sessionmaker(bind=db_engine)
    redis_conn = redis.StrictRedis(**parse_redis_url(redis_url))

def get_value(v: Any) -> Any:
    if isinstance(v, bool):
        return 1 if v else 0
    elif isinstance(v, datetime):
        return v.isoformat()
    else:
        return str(v)

# I hate it
def get_row_dict(row: Base) -> Dict[str, Any]:
    rv = {}
    for c in row.__table__.columns:
        v = getattr(row, c.name)
        if v is None:
            if c.default is not None:
                v = c.default.arg
        if v is not None:
            rv[c.name] = v
    return rv

# This whole mess exists as a way to use the Postgres "ON CONFLICT DO NOTHING"
# clause in order to avoid duplicate inserts as we dump the Redis chat messages
# into Postgres. Without this clause, we'd get IntegrityErrors as messages we'd
# already inserted (but that remained in Redis) were added to the DB again
# (with identical ID tokens). The easy ways to fix this all involve
# roundtripping to the database once for each message, which will likely have
# unacceptable performance. ON CONFLICT DO NOTHING lets us just dump all the
# entries and let Postgres sort them out.

# SQLAlchemy does support the ON CONFLICT DO NOTHING clause, but only in the
# "core" API, not the ORM. There are good reasons for this (the ORM's specific
# database actions during flush are supposed to be opaque to the user), but
# unfortunately it also makes falling back to the core API when all you've got
# are ORM objects into a painful endeavor. Thus we have to handle things like
# column defaults manually in the get_row_dict method above.

def insert_ignoring_duplicates(session: Session, rows: List[Base]) -> None:
    """the members of rows can be any model class"""
    if len(rows) == 0:
        return
    table = rows[0].__table__

    stmt = postgresql.insert(table)
    on_conflict = stmt.on_conflict_do_nothing()

    print("starting session.execute")
    session.execute(on_conflict,
                    [get_row_dict(r) for r in rows])
    print("finished session.execute")

@queue.task(ignore_result=True)
def save_chat_messages():
    """Save all chat messages recorded in the Redis DB to Postgres, and remove old
    messages from Redis. Runs every minute.

    """
    all_channels = redis_conn.smembers('all_channels')
    all_messages = []
    print("save_chat_messages")
    # del_toks = []
    for c in all_channels:
        msgs = [json.loads(i) for i in redis_conn.lrange(c, 0, -1)]
        # del_toks.extend([i['browser_token'] for i in
        #                  msgs[:- message_cache_len]])
        all_messages.extend(msgs)
        redis_conn.ltrim(c, - message_cache_len, -1)

    s = db_session()
    insert_ignoring_duplicates(
        s,
        [ChatMessage.from_dict(i).to_model() for i in all_messages])
    s.commit()
    print("commit done")

    age_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    cutoff_us = age_cutoff.timestamp() * 1000000
    redis_conn.zremrangebyscore('messages_seen', 0, cutoff_us)
