#!python3

import asyncio, json
from datetime import datetime, timezone, timedelta
from .general import db
from .realtime import close_vote
from .data import ChatMessage
from .models import Base, AsyncSession
from . import models

from sqlalchemy.dialects import postgresql

from typing import Any, NoReturn, Collection, cast, Awaitable

# Background workers for quart, used to replace celery

def get_value(v: Any) -> Any:
    if isinstance(v, bool):
        return 1 if v else 0
    elif isinstance(v, datetime):
        return v.isoformat()
    else:
        return str(v)

# I hate it
def get_row_dict(row: Base) -> dict[str, Any]:
    rv = {}
    for c in row.__table__.columns:
        v = getattr(row, c.name)
        if v is None:
            try:
                v = c.default.arg
            except AttributeError:
                pass
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

async def insert_ignoring_duplicates(
        session: AsyncSession, rows: Collection[Base]
) -> None:
    """the members of rows can be any model class"""
    if len(rows) == 0:
        return

    # next(iter(rows)) gets an arbitrary value from rows; we know it won't be
    # empty because of the check above; we do this rather than rows[0] so that
    # we can use Collection rather than Sequence

    # this is a fairly silly thing to do but why not
    obj_type = type(next(iter(rows)))
    stmt = postgresql.insert(obj_type).values(
        [get_row_dict(r) for r in rows]).on_conflict_do_nothing()

    await session.execute(stmt)

async def do_chat_save() -> None:
    """Save all chat messages recorded in the Redis DB to Postgres, and remove
    old messages from Redis. Runs every minute.

    """
    all_channels = await cast(Awaitable[set[Any]], db.redis_conn.smembers('all_channels'))
    all_messages = []
    # print("save_chat_messages")
    # del_toks = []
    for c in all_channels:
        msgs = [json.loads(i) for i in await cast(Awaitable[list[Any]], db.redis_conn.lrange(c, 0, -1))]
        # del_toks.extend([i['browser_token'] for i in
        #                  msgs[:- message_cache_len]])
        all_messages.extend(msgs)
        await cast(Awaitable[str], db.redis_conn.ltrim(c, - message_cache_len, -1))

    # domain invariant: these two sets cover all of all_messages and do not
    # intersect
    user_messages = [ChatMessage.from_dict(i) for i in all_messages
                     if i.get('user_id', None) is not None]
    anon_messages = [ChatMessage.from_dict(i) for i in all_messages
                     if i.get('anon_id', None) is not None]

    async with db.Session() as s:
        await insert_ignoring_duplicates(
            s,
            # [ChatMessage.from_dict(i).to_model() for i in all_messages])
            [i.to_model() for i in user_messages])
        await insert_ignoring_duplicates(
            s,
            # [ChatMessage.from_dict(i).to_model() for i in all_messages])
            [i.to_model() for i in anon_messages])
        await s.commit()
        # print("commit done")

    age_cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    cutoff_us = age_cutoff.timestamp() * 1000000
    await db.redis_conn.zremrangebyscore('messages_seen', 0, cutoff_us)

async def do_address_save() -> None:
    hashes = await cast(Awaitable[dict[Any, Any]], db.redis_conn.hgetall("ip_hashes"))
    hms = [models.AddressIdentifier(hash=k.decode(), ip=v.decode())
           for k, v in hashes.items()]
    async with db.Session() as s:
        await insert_ignoring_duplicates(s, hms)
        await s.commit()
    # this clears out the hash; future hset() invocations in the register_ip
    # function will recreate it
    await db.redis_conn.delete("ip_hashes")

async def chat_save_worker() -> NoReturn:
    while True:
        await asyncio.sleep(60)
        await do_address_save()

async def vote_close_worker() -> NoReturn:
    while True:
        await asyncio.sleep(1)

        now = datetime.now(tz=timezone.utc)
        vals = await db.redis_conn.zrange(
            'vote_close_times',
            0, int(now.timestamp() * 1000), withscores=True, byscore=True)

        async with db.Session() as s:
            for val, score in vals:
                close_time = datetime.fromtimestamp(
                    float(score) / 1000.0, timezone.utc)
                if close_time > now:
                    continue
                print(val, close_time)
                channel_id, vote_id = [int(i) for i in val.decode().split(':')]
                await close_vote(
                    channel_id, vote_id, set_close_time=True,
                    emit_client_event=True, s=s)
