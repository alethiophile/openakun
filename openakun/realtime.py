#!python3

from __future__ import annotations

from . import models, websocket
from .general import (db, db_connect, decode_redis_dict, register_ip,
                      get_user_identifier)
from .data import ChatMessage, Vote, VoteEntry, Message
from quart import render_template, g
from quart import websocket as ws
from functools import wraps
from datetime import datetime, timezone, timedelta
from sqlalchemy import sql, or_, func
from sqlalchemy.sql.expression import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import json
from .websocket import handle_message

from typing import List, Optional, Any, Union, cast, Callable

async def get_channel(channel_id: int) -> models.Channel:
    s = db_connect()
    channel = (await s.scalars(
        select(models.Channel).
        filter(models.Channel.id == channel_id))).one()
    return channel

async def get_story(channel_id: int) -> models.Story:
    s = db_connect()
    story = (await s.scalars(
        select(models.Story).
        filter(models.Story.channel_id == channel_id))).one()
    return story

# user_id can be string ID or 'anon'
async def check_channel_auth(channel_id: int, user_id: int | str) -> bool:
    assert db.redis_conn is not None
    auth_hkey = '{}:{}'.format(user_id, channel_id)
    cv = await db.redis_conn.hget('channel_auth', auth_hkey)
    if cv is None:
        c = await get_channel(channel_id)
        if c.private:
            cv = '0'
        else:
            cv = '1'
        await db.redis_conn.hset('channel_auth', auth_hkey, cv)
    else:
        cv = cv.decode()
    return cv == '1'

def with_channel_auth(err_val: Any = None) -> Callable:
    def return_func(f: Callable) -> Callable:
        @wraps(f)
        async def auth_wrapper(data: dict[str, Any]) -> Any:
            uid = 'anon' if g.current_user is None else g.current_user.id
            if not await check_channel_auth(data['channel'], uid):
                return err_val
            return await f(data)
        return auth_wrapper
    return return_func

@handle_message('backlog')
# @with_channel_auth({ 'success': False, 'error': 'Channel is private' })
async def handle_backlog(data: dict[str, Any]) -> Any:
    print("Sending backlog for channel", data['channel'])
    bl = await get_back_messages(data['channel'])
    await send_back_messages(bl, g.websocket_id)
    return { 'success': True }

async def send_back_messages(msgs: List[ChatMessage], to: str) -> None:
    for i in msgs:
        mo = i.to_browser_message()
        if mo['is_anon']:
            mo['username'] = 'anon'
        html = await render_template('render_chatmsg.html', c=mo, htmx=True)
        await websocket.pubsub.publish(to, html)

page_len = 100

async def get_thread_messages(cid: int, tid: int) -> list[ChatMessage]:
    s = db_connect()
    q = (select(models.ChatMessage).
         filter(models.ChatMessage.channel_id == cid).
         filter(or_(models.ChatMessage.thread_id == tid,
                    models.ChatMessage.id == tid)).
         order_by(models.ChatMessage.date.desc()))
    db_msgs = list((await s.scalars(q)).all())
    msgs = [ChatMessage.from_model(i) for i in reversed(db_msgs)]
    return msgs

async def get_recent_backlog(cid: int) -> list[ChatMessage]:
    s = db_connect()
    q = (select(models.ChatMessage).
         options(selectinload(models.ChatMessage.thread_head)).
         filter(models.ChatMessage.channel_id == cid).
         order_by(models.ChatMessage.date.desc()).
         limit(page_len))
    db_msgs = list((await s.scalars(q)).all())
    db_msgs.reverse()
    msgs = [ChatMessage.from_model(i) for i in db_msgs]
    return msgs

async def get_page_list(cid: int) -> list[tuple[int, int, datetime]]:
    """Given a channel ID, return the list of pages.

    Pages are not fixed, they're just slices by page_len of the whole channel
    backlog. You don't request a page number, you request a slice starting at
    the appropriate datetime.

    Elements of the return list are tuples: (page_num: int, message_id: int,
    message_date: datetime).
    """
    s = db_connect()
    subq = (select(models.ChatMessage.id, models.ChatMessage.date,
                   models.ChatMessage.channel_id,
                   func.row_number().over(order_by=models.ChatMessage.date).
                   label("row_num")).
            filter(models.ChatMessage.channel_id == cid).
            subquery())
    res = (await s.execute(
        select(subq.c.id, subq.c.date).
        filter(subq.c.row_num % page_len == 1)
    )).all()
    return [(n, i, d) for (n, (i, d)) in enumerate(res)]

@handle_message('chat_message')
@with_channel_auth()
async def handle_chat(data: dict[str, Any]) -> None:
    print("Chat message", data, ws.remote_addr, g.current_user)
    if len(data['msg']) == 0:
        return

    c_ts = datetime.now(tz=timezone.utc)

    channel_id = data['channel']
    if g.current_user is None:
        assert ws.remote_addr is not None
        hashval = await register_ip(ws.remote_addr)
    user_info = (
        { 'anon_id': hashval } if g.current_user is None else
        { 'user_id': g.current_user.id, 'user_name': g.current_user.name })
    thread_id = int(data['thread_id']) if data['thread_id'] else None
    msg = ChatMessage.new(
        msg_text=data['msg'],
        channel_id=channel_id,
        date=c_ts,
        thread_id=thread_id,
        **user_info)

    s = db_connect()
    db_msg = msg.to_model()
    s.add(db_msg)
    await s.commit()

    msg = ChatMessage.from_model(db_msg)
    mo = msg.to_browser_message()
    if mo['is_anon']:
        # TODO eventually set this to the story-configured anon username
        mo['username'] = 'anon'
    html = await render_template('render_chatmsg.html', c=mo, htmx=True)
    await websocket.pubsub.publish(f'chan:{channel_id}', html)

async def add_active_vote(vm: models.VoteInfo, channel_id: int,
                          s: AsyncSession | None = None) -> None:
    """This function takes trusted input: it gets called when a new vote is
    created, and its parameters come from pages.new_post(), which verifies the
    data.

    """
    if s is None:
        s = db_connect()
    vote = await Vote.from_model_dbload(s, vm)
    assert vote.db_id is not None
    assert db.redis_conn is not None

    rd = vote.to_redis_dict()
    rd['channel_id'] = channel_id
    rds = json.dumps(rd)
    await db.redis_conn.hset('vote_info', str(vote.db_id), rds)

    if vote.close_time is not None:
        await db.redis_conn.zadd('vote_close_times',
                                 { f"{channel_id}:{vote.db_id}":
                                   int(vote.close_time.timestamp() * 1000) })

    channel_key = f"channel_votes:{channel_id}"
    await db.redis_conn.sadd(channel_key, str(vote.db_id))

async def repopulate_from_db() -> None:
    async with db.Session() as s:
        votes = (await s.scalars(
            select(models.VoteInfo).
            options(selectinload(models.VoteInfo.votes).
                    selectinload(models.VoteEntry.votes),
                    selectinload(models.VoteInfo.post).
                    selectinload(models.Post.story)).
            where((models.VoteInfo.time_closed > sql.functions.now()) |
                  (models.VoteInfo.time_closed == None)))).all()
        for vm in votes:
            print(vm)
            channel_id = vm.post.story.channel_id
            await add_active_vote(vm, channel_id, s)

async def vote_is_active(channel_id: int, vote_id: int) -> bool:
    assert db.redis_conn is not None
    channel_key = f"channel_votes:{channel_id}"
    return await db.redis_conn.sismember(channel_key, vote_id)

async def populate_vote(channel_id: int, vote: Vote) -> Vote:
    """Takes a vote, populates its options with the
    killed/killed_text/vote_count items and it with active. vote is populated
    in-place, return value is always the same object. This is called by the Web
    endpoints in order to render active votes in new loads.

    """
    assert db.redis_conn is not None
    assert vote.db_id is not None

    if not await vote_is_active(channel_id, vote.db_id):
        vote.active = False
        return vote

    vote.active = True

    rds: str = cast(str,
                    await db.redis_conn.hget('vote_info', str(vote.db_id)))
    rd = json.loads(rds)
    vote.update_redis_dict(rd)

    return vote

async def get_vote_object(channel_id: int, vote_id: int) -> Optional[Vote]:
    s = db_connect()
    vm = (await s.scalars(
        select(models.VoteInfo).filter(models.VoteInfo.id == vote_id)
    )).one_or_none()
    if vm is None:
        return None
    v = await Vote.from_model_dbload(s, vm)
    await populate_vote(channel_id, v)
    return v

async def send_vote_html(
        channel_id: int, vote_id: int, reopen: bool = False
) -> None:
    """Render a vote in user-agnostic form (i.e. no voted-for annotations) and
    send the resulting HTML over the channel. This is called every time a vote
    is altered (votes added or removed, options added, config changed, etc.) in
    order to update clients' views.

    """
    s = db_connect()
    m = (await s.scalars(
        select(models.VoteInfo).
        options(selectinload(models.VoteInfo.post).
                selectinload(models.Post.story).
                selectinload(models.Story.author)).
        filter(models.VoteInfo.id == vote_id)
    )).one()
    v = await Vote.from_model_dbload(s, m)
    await populate_vote(channel_id, v)
    # this is a dummy chapter object used only to get the channel ID
    dummy_chapter = { 'story': { 'channel_id': channel_id }}
    # in this case the public update will have vote totals hidden; we draw a
    # special update with totals shown, and send it only to the story author
    if v.votes_hidden and v.active:
        author_id = f"user:{m.post.story.author_id}"
        html = await render_template('render_vote.html', vote=v,
                                     morph_swap=True, is_author=True,
                                     chapter=dummy_chapter)
        await websocket.pubsub.publish(author_id, html)
    html = await render_template('render_vote.html', vote=v, morph_swap=True,
                                 chapter=dummy_chapter)
    await websocket.pubsub.publish(f'chan:{channel_id}', html)

@handle_message('add_vote')
@with_channel_auth()
async def handle_add_vote(data: dict[str, Any]) -> None:
    """Takes an info dictionary of the form:
    { 'channel': channel_id, 'vote': vote_id, 'option': option_id }

    """
    channel_id = data['channel']
    vote_id = data['vote']
    option_id = data['option']

    if not await vote_is_active(channel_id, vote_id):
        return None

    uid = await get_user_identifier()
    rv = await db.redis_conn.fcall(
        'add_vote', 2,
        f'channel_votes:{channel_id}',
        'vote_info', vote_id, option_id, uid)
    if not rv:
        return

    conf = json.loads(
        cast(str, await db.redis_conn.hget('vote_info', str(vote_id))))
    print(conf)
    pl = { 'vote': vote_id, 'option': option_id, 'value': True,
           'clear': False }
    if not conf['multivote']:
        pl['clear'] = True
    m = Message(message_type='user-vote', data=pl, dest=uid)
    await m.send()
    await send_vote_html(int(channel_id), int(vote_id))

@handle_message('remove_vote')
@with_channel_auth()
async def handle_remove_vote(data: dict[str, Any]) -> None:
    assert db.redis_conn is not None

    channel_id = data['channel']
    vote_id = data['vote']
    option_id = data['option']

    if not await vote_is_active(channel_id, vote_id):
        return None

    uid = await get_user_identifier()

    rv = await db.redis_conn.fcall(
        'remove_vote', 2,
        f'channel_votes:{channel_id}',
        'vote_info', vote_id, option_id, uid)
    if not rv:
        return

    m = Message(message_type='user-vote',
                data={ 'vote': vote_id, 'option': option_id, 'value': False,
                       'clear': False }, dest=uid)
    await m.send()
    await send_vote_html(int(channel_id), int(vote_id))

@handle_message('new_vote_entry')
@with_channel_auth()
async def handle_new_vote_entry(data: dict[str, Any]) -> None:
    assert db.redis_conn is not None

    channel_id = data['channel']
    vote_id = data['vote']
    # voteinfo = data['vote_info']
    option_text = data['option_text']

    if not await vote_is_active(channel_id, vote_id):
        return None

    conf = json.loads(
        cast(str, await db.redis_conn.hget('vote_info', str(vote_id))))
    if not conf['writein_allowed']:
        return None

    option_text = option_text.strip()
    if not option_text:
        return None
    option = VoteEntry(text=option_text)
    # option = VoteEntry.from_dict(voteinfo)
    # # the only thing we accept from the client is the option text
    # option.killed = False
    # option.killed_text = None
    # option.vote_count = 1
    # option.db_id = None

    # option.text = option.text.strip()
    # if not option.text:
    #     return None

    om = option.create_model()
    om.vote_id = vote_id
    s = db_connect()
    s.add(om)
    await s.commit()

    # this picks up the db_id just set by Postgres
    option = VoteEntry.from_model(om)
    assert option.db_id is not None

    uid = await get_user_identifier()
    rv = await db.redis_conn.fcall(
        'new_vote_entry', 2,
        f'channel_votes:{channel_id}',
        'vote_info', vote_id, option.db_id, uid)

    # we check whether writeins are allowed within the Redis function, to
    # ensure atomicity; a false value means that that check (or another
    # correctness check) failed, so the Postgres object we just created should
    # be destroyed

    # this should only happen in case of a race condition; usually attempts at
    # creating a new entry when writein_allowed is false will be fenced out by
    # the within-Python check above
    if not rv:
        await s.delete(om)
        await s.commit()
        return

    m = Message(message_type='user-vote',
                data={ 'vote': vote_id, 'option': option.db_id, 'value': True,
                       'clear': False }, dest=uid)
    await m.send()
    await send_vote_html(int(channel_id), int(vote_id))

async def get_user_votes(
        vote_id: Union[int, str], user_id: Optional[str] = None) -> set[int]:
    """Takes a vote ID and a user identifier, which is either a string with
    "user:<userid>", or a hex IP hash. Returns a set of option IDs."""
    assert db.redis_conn is not None

    if user_id is None:
        user_id = await get_user_identifier()
    user_id = str(user_id)

    rv: set[int] = set()
    vis = await db.redis_conn.hget('vote_info', str(vote_id))
    if not vis:
        return rv
    vote = json.loads(vis)
    for oid, opt in vote['votes'].items():
        if user_id in opt['users_voted_for']:
            rv.add(int(oid))

    return rv

# @handle_message('get_my_votes')
# def request_my_votes(data) -> None:
#     vote_id = data['vote']

#     votes = get_user_votes(vote_id)
#     for v in votes:
#         emit('user_vote', { 'vote': vote_id, 'option': v, 'value': True })

# To close a vote, the following needs to be done:
#
#  - the vote config in Postgres (on the vote_info row) updated from the Redis
#    vote_config key, and that key deleted
#  - the killed options updated from the options_killed hash, and any options
#    on the vote removed from that hash
#  - the user_votes updated from option_votes, and all option_votes keys for
#    options on the vote deleted
#  - the vote_options key deleted (all this info should already be in postgres)
#  - the vote removed from the channel_votes set
#  - the time_closed set on the vote_info row
#  - the vote_closed event emitted to the frontend
async def close_vote(
        channel_id: int, vote_id: int, set_close_time: bool = True,
        emit_client_event: bool = True, s: AsyncSession | None = None
) -> None:
    """This function trusts its input: caller must verify that the given vote
    is open on the given channel.

    set_close_time is used to check whether the vote's close time is set to
    now. This should be true if a vote is being closed "for real" (due to user
    action or timer expiration), false if it's being closed on shutdown for
    later repopulation. If a vote has a close time set to a point in the
    future, that timer will still tick while closed for repopulation; if the
    close time passes during the downtime, the vote will stay closed and not
    repopulate.

    emit_client_event governs whether an event is emitted to the frontend
    marking the vote close. It should usually be true if set_close_time is
    true.

    """
    assert db.redis_conn is not None

    if not await vote_is_active(channel_id, vote_id):
        return None

    if s is None:
        s = db_connect()
    vm = (await s.scalars(
        select(models.VoteInfo).filter(models.VoteInfo.id == vote_id))).one()
    ve = await Vote.from_model_dbload(s, vm)

    await populate_vote(channel_id, ve)

    channel_key = f"channel_votes:{channel_id}"
    removed = await db.redis_conn.srem(channel_key, str(vote_id))
    # srem() returns the number of items removed from the set; in this case
    # that will be 1 or 0 depending on whether the ID was actually in the set

    # if it's 0, that means we got a race condition where someone else beat us
    # to removing the item, so just exit
    if removed < 1:
        return None

    # these parameters don't matter when the vote is closed but they're
    # preserved for if it opens again
    vm.multivote = ve.multivote
    vm.writein_allowed = ve.writein_allowed
    vm.votes_hidden = ve.votes_hidden

    # for closed votes, time_closed represents the actual time of closure, so
    # just set it to the current time
    if set_close_time:
        vm.time_closed = datetime.now(tz=timezone.utc)
    else:
        vm.time_closed = ve.close_time

    ed = { i.db_id: i for i in ve.votes }
    for v in vm.votes:
        v.killed = ed[v.id].killed
        v.killed_text = ed[v.id].killed_text

        vl = ed[v.id].users_voted_for
        assert vl is not None
        v.votes.clear()
        for u in vl:
            if u.startswith('user:'):
                user_id = int(u[5:])
                um = models.UserVote(user_id=user_id)
            else:
                anon_id = u[5:]
                um = models.UserVote(anon_id=anon_id)
            v.votes.append(um)
    await s.commit()

    await db.redis_conn.zrem('vote_close_times', f"{channel_id}:{ve.db_id}")
    await db.redis_conn.hdel('vote_info', str(vote_id))

    if emit_client_event:
        await websocket.pubsub.publish(f'chan:{channel_id}',
                                       json.dumps({ 'type': 'set-vote-open',
                                                    'vote_id': vote_id,
                                                    'open': False }))

async def close_to_db() -> None:
    s = db.Session()
    vl = await db.redis_conn.hgetall('vote_info')
    vd = decode_redis_dict(vl)
    for vid, vs in vd.items():
        vi = json.loads(vs)
        channel_id = vi['channel_id']
        await close_vote(channel_id, int(vid), set_close_time=False,
                         emit_client_event=False, s=s)

# This can just call add_active_vote again, unset time_closed on the vote
# entry, and emit an event to the frontend
async def open_vote(channel_id: int, vote_id: int) -> None:
    s = db_connect()
    vm = (await s.scalars(
        select(models.VoteInfo).
        options(selectinload(models.VoteInfo.post).
                selectinload(models.Post.story)).
        where(models.VoteInfo.id == vote_id))).one()
    channel_id = vm.post.story.channel_id
    vm.time_closed = None
    await add_active_vote(vm, channel_id)
    await s.commit()

    await websocket.pubsub.publish(
        f'chan:{channel_id}',
        json.dumps({ 'type': 'set-vote-open',
                     'vote_id': vote_id, 'open': True }))

@handle_message('set_vote_active')
async def set_vote_active(data: dict[str, Any]) -> None:
    channel_id = int(data['channel'])
    story = await get_story(channel_id)

    # TODO do we want to think about multiple authors?
    if story.author != g.current_user:
        return

    vote_id = int(data['vote'])
    set_active = data['active']
    now_active = await vote_is_active(channel_id, vote_id)

    if now_active == set_active:
        return

    if not set_active:
        await close_vote(channel_id, vote_id)
    else:
        await open_vote(channel_id, vote_id)

@handle_message('set_option_killed')
async def set_option_killed(data: dict[str, Any]) -> None:
    assert db.redis_conn is not None

    channel_id = data['channel']
    story = await get_story(channel_id)

    if story.author != g.current_user:
        return

    vote_id = data['vote']
    option_id = data['option']
    killed = '1' if data['killed'] else '0'
    kill_string = data.get('message', '')

    if not await vote_is_active(channel_id, vote_id):
        return

    rv = await db.redis_conn.fcall(
        'set_option_killed', 2,
        f'channel_votes:{channel_id}',
        'vote_info', vote_id, option_id, killed,
        kill_string)
    if not rv:
        return None

    await send_vote_html(int(channel_id), int(vote_id))

@handle_message('set_vote_options')
async def set_vote_options(data: dict[str, Any]) -> None:
    # TODO factor out this authentication code
    channel_id = int(data['channel'])
    story = await get_story(channel_id)
    vote_id = int(data['vote'])

    if story.author != g.current_user:
        return

    if not await vote_is_active(channel_id, vote_id):
        return

    # TODO handle close time here as well
    multivote = data.get('multivote') == 'true'
    writein_allowed = data.get('writein_allowed') == 'true'
    votes_hidden = data.get('votes_hidden') == 'true'
    # possible keys are ['multivote', 'writein_allowed', 'votes_hidden',
    # 'close_time']

    vote = await get_vote_object(channel_id, vote_id)
    assert vote is not None

    # attempts to turn multivote off are ignored, since this could require
    # deleting user votes
    if multivote:
        vote.multivote = True

    vote.writein_allowed = writein_allowed
    vote.votes_hidden = votes_hidden

    rv = await db.redis_conn.fcall(
        'set_vote_config', 2,
        f'channel_votes:{channel_id}', 'vote_info',
        vote_id, json.dumps(vote.to_redis_dict()))
    if not rv:
        return None

    await send_vote_html(channel_id, vote_id)

@handle_message('set_vote_close_time')
async def set_vote_close_time(data: dict[str, Any]) -> None:
    # TODO factor out this authentication code
    channel_id = int(data['channel'])
    story = await get_story(channel_id)
    vote_id = int(data['vote'])

    if story.author != g.current_user:
        return

    if not await vote_is_active(channel_id, vote_id):
        return

    # TODO handle close time here as well

    vote = await get_vote_object(channel_id, vote_id)
    assert vote is not None

    if data.get('clear'):
        vote.close_time = None
    else:
        try:
            val = data.get('value')
            assert isinstance(val, (str, int))
            num = int(val)
        except Exception:
            return
        unit = data.get('unit')
        if unit == 'minutes':
            td = num * 60
        elif unit == 'hours':
            td = num * 3600
        else:
            return
        vote.close_time = (datetime.now(tz=timezone.utc).replace(microsecond=0)
                           + timedelta(seconds=td))

    rd = {}
    rd['close_time'] = vote.to_redis_dict()['close_time']

    rv = await db.redis_conn.fcall(
        'set_vote_config', 2,
        f'channel_votes:{channel_id}', 'vote_info',
        vote_id, json.dumps(rd))
    if not rv:
        return None

    if vote.close_time is not None:
        await db.redis_conn.zadd('vote_close_times',
                                 { f"{channel_id}:{vote.db_id}":
                                   int(vote.close_time.timestamp() * 1000) })
    else:
        await db.redis_conn.zrem('vote_close_times',
                                 f"{channel_id}:{vote.db_id}")

    await send_vote_html(channel_id, vote_id)
