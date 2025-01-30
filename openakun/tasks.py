#!python3

from .general import parse_redis_url, db_setup, db
from .app import get_config
from .models import Base
from .realtime import message_cache_len, ChatMessage, close_vote
from .websocket import pubsub

import celery, redis, json, os
from celery.signals import worker_process_init, worker_process_shutdown

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from sqlalchemy.dialects import postgresql

from datetime import datetime, timezone, timedelta

from typing import Dict, Any, List

config_fn = os.environ.get("OPENAKUN_CONFIG", 'openakun.cfg')
config = get_config(config_fn)

redis_url = config['openakun']['redis_url']
queue = celery.Celery()
queue.conf.update(
    broker_url=redis_url,
    result_backend=redis_url,
)

@queue.on_after_configure.connect
def setup_periodic(sender, **kwargs):
    sender.add_periodic_task(60.0, save_chat_messages.s())
    sender.add_periodic_task(60.0, queue_vote_closures.s())

@worker_process_init.connect
def get_db_conn(**kwargs):
    config_fn = os.environ.get("OPENAKUN_CONFIG", 'openakun.cfg')
    config = get_config(config_fn)
    db_setup(config=config)
    pubsub.set_redis_opts(redis_url=config['openakun']['redis_url'],
                          redis_send=True, redis_recv=False)

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
    all_channels = db.redis_conn.smembers('all_channels')
    all_messages = []
    print("save_chat_messages")
    # del_toks = []
    for c in all_channels:
        msgs = [json.loads(i) for i in db.redis_conn.lrange(c, 0, -1)]
        # del_toks.extend([i['browser_token'] for i in
        #                  msgs[:- message_cache_len]])
        all_messages.extend(msgs)
        db.redis_conn.ltrim(c, - message_cache_len, -1)

    # domain invariant: these two sets cover all of all_messages and do not
    # intersect
    user_messages = [ChatMessage.from_dict(i) for i in all_messages
                     if i.get('user_id', None) is not None]
    anon_messages = [ChatMessage.from_dict(i) for i in all_messages
                     if i.get('anon_id', None) is not None]

    with db.Session() as s:
        insert_ignoring_duplicates(
            s,
            # [ChatMessage.from_dict(i).to_model() for i in all_messages])
            [i.to_model() for i in user_messages])
        insert_ignoring_duplicates(
            s,
            # [ChatMessage.from_dict(i).to_model() for i in all_messages])
            [i.to_model() for i in anon_messages])
        s.commit()
        print("commit done")

    age_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    cutoff_us = age_cutoff.timestamp() * 1000000
    db.redis_conn.zremrangebyscore('messages_seen', 0, cutoff_us)

@queue.task(ignore_result=True)
def queue_vote_closures():
    """This task runs every minute. It scans the DB for votes that are set to
    close within the next minute, and spawns new tasks to close them. This
    avoids the problem with passing far-future eta arguments to celery tasks.

    """
    now = datetime.now(tz=timezone.utc)
    end = now + timedelta(minutes=1)
    # we start from 0, i.e. 1970, to catch any votes with close times in the
    # past
    start = datetime.datetime.fromtimestamp(0.0)
    print("queue_vote_closures", start, end)
    vals = db.redis_conn.zrange('vote_close_times', start.timestamp() * 1000,
                                end.timestamp() * 1000, withscores=True)
    print("vals", vals)

    for val, score in vals:
        close_time = datetime.fromtimestamp(float(score) / 1000.0,
                                            timezone.utc)
        channel_id, vote_id = [int(i) for i in val.split(':')]
        do_close_vote.apply_async((channel_id, vote_id), eta=close_time)

@queue.task(ignore_result=True)
def do_close_vote(channel_id: int, vote_id: int):
    print("do_close_vote", channel_id, vote_id)
    close_vote(channel_id, vote_id, set_close_time=True,
               emit_client_event=True, s=db.Session())
