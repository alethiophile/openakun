#!python3

from __future__ import annotations

from quart import request, Blueprint, websocket, g

import threading, json, os, traceback, secrets, asyncio
from asyncio import Queue, Task
import redis.asyncio as redis

from . import realtime

import typing as t

# Concurrency analysis: Each channel has a set of subscriptions, represented as
# a dict with values of SimpleQueue and keys of id(value). The set of
# subscriptions is guarded by a lock.
#
# Every time a subscription is added or removed, the subscription's queue is
# added/removed from the set for each channel on the subscription. The lock for
# that channel is held while doing this; each lock is held only while modifying
# the relevant channel's subscription set (the duration of a dict set/del
# operation).
#
# Every time a value is published to a channel, the publisher iterates the
# whole subscription set for that channel, writing the value to each queue. The
# lock for the channel is held while doing this. In theory this could be
# replaced with a reader/writer lock, to allow multiple publishers to write
# concurrently. In practice, publishing is O(n) and we don't expect more than a
# few hundred subscriptions per channel, so performance is unlikely to be a
# huge issue.
#
# If multiple publishers write to different channels concurrently, and one
# subscription has multiple of those channels, the publishers may concurrently
# write to the same queue. Queues are thread-safe internally, so this is fine.
class SubscriptionFanout[T]:
    """A simple pubsub implementation based on queues.

    Channels are identified by string keys. Every subscription is a single
    queue. A subscription may be to any number of channels, but the set of
    channels is unchangeable over the life of the subscription.

    Each channel has a set of subscriptions (queues) and a lock (guarding
    changes to the set). A new subscription creates a new queue and adds it to
    the set for every channel subscribed to. Publishing to a channel means
    iterating over the list of subscriptions and putting the value in each
    one's queue.

    Warning: the value published to the channel is shared across every
    subscriber that receives it. Thus, receivers should all treat the values
    they receive as read-only. If the sender or any receiver modifies these
    values, concurrency errors may result.

    """
    REDIS_PS_CHAN = 'subscription-fanout'

    def __init__(self, redis_url: str | None = None,
                 redis_send: bool = False,
                 redis_recv: bool = False) -> None:
        self.queues: dict[str, dict[int, Queue]] = {}
        self.locks: dict[str, threading.Lock] = {}

        self.set_redis_opts(redis_url, redis_send, redis_recv)

    def set_redis_opts(self, redis_url: str | None,
                       redis_send: bool = False,
                       redis_recv: bool = False) -> None:
        if hasattr(self, 'redis_conn'):
            raise RuntimeError("cannot set redis opts again")

        if (redis_send or redis_recv) and not redis_url:
            raise ValueError("must set redis_url if send or receive selected")

        if not (redis_send or redis_recv):
            return

        assert redis_url is not None
        self.redis_url = redis_url
        self.redis_send = redis_send
        self.redis_recv = redis_recv
        self._recv_task: Task | None = None
        self.obj_id = f"{os.getpid()}-{id(self)}"

    def __enter__(self) -> SubscriptionFanout[T]:
        self.redis_conn = redis.Redis.from_url(self.redis_url)
        if self.redis_recv:
            self._recv_task = asyncio.create_task(self._redis_msg_receiver())

        return self

    def __exit__(
            self, exc_type: t.Any, exc_value: t.Any, traceback: t.Any
    ) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()

    async def _redis_msg_receiver(self) -> t.NoReturn:
        ps = self.redis_conn.pubsub(ignore_subscribe_messages=True)
        await ps.subscribe(self.REDIS_PS_CHAN)
        async for message in ps.listen():
            try:
                key, val, sender = self._parse_redis_msg(message['data'])
            except Exception:
                traceback.print_exc()
                continue
            if sender == self.obj_id:
                continue
            await self._internal_publish(key, val)
        # should never reach here
        raise RuntimeError()

    def _make_redis_msg(self, key: str, value: t.Any) -> str:
        return json.dumps({ 'key': key, 'value': value,
                            'sender': self.obj_id })

    @staticmethod
    def _parse_redis_msg(msg: str) -> tuple[str, t.Any, str]:
        d = json.loads(msg)
        return d['key'], d['value'], d['sender']

    async def _internal_publish(self, key: str, value: t.Any) -> None:
        with self.locks.setdefault(key, threading.Lock()):
            for q in self.queues.setdefault(key, {}).values():
                await q.put((key, value))

    async def publish(self, key: str, value: T) -> None:
        await self._internal_publish(key, value)
        if self.redis_send:
            message = self._make_redis_msg(key, value)
            await self.redis_conn.publish(self.REDIS_PS_CHAN, message)

    async def subscribe(
            self, *keys: str, timeout: t.Optional[float] = None
    ) -> t.AsyncGenerator[t.Tuple[str, T], None]:
        """Subscribes to a set of channels, then yields all the messages that
        come in over those channels.

        """
        q: Queue[tuple[str, T]] = Queue()
        for k in keys:
            with self.locks.setdefault(k, threading.Lock()):
                self.queues.setdefault(k, {})[id(q)] = q

        loop = asyncio.get_running_loop()
        time_end = loop.time() + timeout if timeout is not None else None
        try:
            while True:
                if time_end is not None:
                    now = loop.time()
                    if now > time_end:
                        return
                    cto = time_end - now
                else:
                    cto = None
                try:
                    async with asyncio.timeout_at(cto):
                        val = await q.get()
                except asyncio.TimeoutError:
                    continue
                yield val
        finally:
            for k in keys:
                with self.locks[k]:
                    del self.queues[k][id(q)]
                    if len(self.queues[k]) <= 0:
                        del self.queues[k]
                        del self.locks[k]

# global
pubsub: SubscriptionFanout[str] = SubscriptionFanout()

rtb = Blueprint('realtime', __name__)

handlers = {}

def handle_message(which: str) -> t.Callable:
    def deco(fn: t.Callable) -> t.Callable:
        handlers[which] = fn
        return fn
    return deco

@rtb.websocket('/ws/<channel>')
async def ws_endpoint(channel: str) -> None:
    print("current user is", str(g.current_user), channel)

    ws_chan = f'ws:{secrets.token_urlsafe()}'
    channel_chan = f'chan:{channel}'
    user_chan = await realtime.get_user_identifier()

    async def downsender() -> None:
        async for chan, msg in pubsub.subscribe(
                channel_chan, ws_chan, user_chan):
            if chan == ws_chan and msg == 'ws_quit':
                break
            await websocket.send(msg)

    g.websocket_id = ws_chan

    asyncio.create_task(downsender())
    while True:
        try:
            data = await websocket.receive()
        except asyncio.CancelledError:
            await pubsub.publish(ws_chan, 'ws_quit')
            raise
        # print("got websocket data:", data)
        msg = json.loads(data)
        try:
            msg['channel'] = int(channel)
        except ValueError:
            msg['channel'] = channel
        mtype = msg.get('type', None)
        if mtype is None:
            continue
        fn = handlers.get(mtype)
        if fn is None:
            continue
        await fn(msg)
