#!python3

from __future__ import annotations

from . import models, websocket
from .general import db, db_connect, db_setup, decode_redis_dict
from .data import ChatMessage, Vote, VoteEntry, Message
from flask_login import current_user
from flask import request, render_template
from functools import wraps
from operator import attrgetter
from datetime import datetime, timezone, timedelta
from sqlalchemy import sql, orm
import hashlib, json
from .websocket import handle_message

from typing import List, Dict, Optional, Any, Union

def get_channel(channel_id):
    s = db_connect()
    channel = (s.query(models.Channel).
               filter(models.Channel.id == channel_id).one())
    return channel

def get_story(channel_id):
    s = db_connect()
    story = (s.query(models.Story).
             filter(models.Story.channel_id == channel_id).one())
    return story

# user_id can be string ID or 'anon'
def check_channel_auth(channel_id, user_id):
    assert db.redis_conn is not None
    auth_hkey = '{}:{}'.format(user_id, channel_id)
    cv = db.redis_conn.hget('channel_auth', auth_hkey)
    if cv is None:
        c = get_channel(channel_id)
        if c.private:
            cv = '0'
        else:
            cv = '1'
        db.redis_conn.hset('channel_auth', auth_hkey, cv)
    else:
        cv = cv.decode()
    return cv == '1'

def with_channel_auth(err_val=None):
    def return_func(f):
        @wraps(f)
        def auth_wrapper(data):
            uid = 'anon' if current_user.is_anonymous else current_user.id
            if not check_channel_auth(data['channel'], uid):
                return err_val
            return f(data)
        return auth_wrapper
    return return_func

@handle_message('backlog')
#@with_channel_auth({ 'success': False, 'error': 'Channel is private' })
def handle_backlog(data):
    print("Sending backlog for channel", data['channel'])
    bl = get_back_messages(data['channel'])
    send_back_messages(bl, request.sid)
    return { 'success': True }

def send_back_messages(msgs: List[ChatMessage], to: str) -> None:
    for i in msgs:
        mo = i.to_browser_message()
        if mo['is_anon']:
            mo['username'] = 'anon'
        html = render_template('render_chatmsg.html', c=mo, htmx=True)
        websocket.pubsub.publish(to, html)

def register_ip(addr):
    # TODO make this use Redis and a periodic sweep, like chat messages
    hashval = hashlib.sha256(addr.encode()).hexdigest()
    s = db_connect()
    q_num = (s.query(models.AddressIdentifier).
             filter(models.AddressIdentifier.hash == hashval).count())
    if (q_num == 0):
        ai = models.AddressIdentifier(hash=hashval, ip=addr)
        s.add(ai)
    s.commit()
    return hashval

message_cache_len = 60

def get_back_messages(cid: int) -> List[ChatMessage]:
    assert db.redis_conn is not None
    rkey = f"channel_messages:{cid}"
    msgs = [ChatMessage.from_dict(json.loads(i.decode())) for i in
            db.redis_conn.lrange(rkey, - message_cache_len, -1)]
    redis_msg_count = len(msgs)
    if redis_msg_count < message_cache_len:
        s = db_connect()
        rv = (s.query(models.ChatMessage).
              filter(models.ChatMessage.channel_id == cid).
              order_by(models.ChatMessage.date.desc()).
              limit(message_cache_len).
              all())
        toks = set(i.server_token for i in msgs)
        db_msgs = [ChatMessage.from_model(i) for i in rv
                   if i.id_token not in toks]
        msgs = list(reversed(db_msgs)) + msgs
        msgs.sort(key=attrgetter('date'))
        msgs = msgs[- message_cache_len:]
        if len(msgs) > redis_msg_count:
            pass
    return msgs

@handle_message('chat_message')
@with_channel_auth()
def handle_chat(data) -> None:
    print("Chat message", data, request.remote_addr, current_user)
    if len(data['msg']) == 0:
        return

    assert db.redis_conn is not None
    # ensure we haven't seen this message before

    # this uses a sorted set where the sort value is the timestamp, allowing us
    # to quickly drop old data
    token = data['id_token']
    if db.redis_conn.zscore('messages_seen', token) is not None:
        return
    c_ts = datetime.now(tz=timezone.utc)
    us_now = c_ts.timestamp() * 1000000
    db.redis_conn.zadd('messages_seen', { token: us_now })

    channel_id = data['channel']
    if current_user.is_anonymous:
        hashval = register_ip(request.remote_addr)
    user_info = (
        { 'anon_id': hashval } if current_user.is_anonymous else
        { 'user_id': current_user.id, 'user_name': current_user.name })
    msg = ChatMessage.new(
        browser_token=token,
        msg_text=data['msg'],
        channel_id=channel_id,
        date=c_ts,
        **user_info)

    rkey = f"channel_messages:{channel_id}"
    val = json.dumps(msg.to_dict())
    db.redis_conn.rpush(rkey, val)

    db.redis_conn.sadd('all_channels', rkey)

    mo = msg.to_browser_message()
    if mo['is_anon']:
        # TODO eventually set this to the story-configured anon username
        mo['username'] = 'anon'
    html = render_template('render_chatmsg.html', c=mo, htmx=True)
    websocket.pubsub.publish(f'chan:{channel_id}', html)

def add_active_vote(vm: models.VoteInfo, channel_id: int) -> None:
    """This function takes trusted input: it gets called when a new vote is
    created, and its parameters come from pages.new_post(), which verifies the
    data.

    """
    vote = Vote.from_model(vm)
    assert vote.db_id is not None
    assert db.redis_conn is not None

    rd = vote.to_redis_dict()
    rd['channel_id'] = channel_id
    rds = json.dumps(rd)
    db.redis_conn.hset('vote_info', str(vote.db_id), rds)

    if vote.close_time is not None:
        db.redis_conn.zadd('vote_close_times',
                           { f"{channel_id}:{vote.db_id}":
                             int(vote.close_time.timestamp() * 1000) })

    channel_key = f"channel_votes:{channel_id}"
    db.redis_conn.sadd(channel_key, str(vote.db_id))

def repopulate_from_db():
    s = db.Session()
    votes = (s.query(models.VoteInfo).
             where((models.VoteInfo.time_closed > sql.functions.now()) |
                   (models.VoteInfo.time_closed == None)).all())
    for vm in votes:
        print(vm)
        channel_id = vm.post.story.channel_id
        add_active_vote(vm, channel_id)

def vote_is_active(channel_id: int, vote_id: int) -> bool:
    assert db.redis_conn is not None
    channel_key = f"channel_votes:{channel_id}"
    return db.redis_conn.sismember(channel_key, vote_id)

def populate_vote(channel_id: int, vote: Vote) -> Vote:
    """Takes a vote, populates its options with the killed/killed_text/vote_count
    items and it with active. vote is populated in-place, return value is
    always the same object. This is called by the Web endpoints in order to
    render active votes in new loads.

    """
    assert db.redis_conn is not None
    assert vote.db_id is not None

    if not vote_is_active(channel_id, vote.db_id):
        vote.active = False
        return vote

    vote.active = True

    rds = db.redis_conn.hget('vote_info', str(vote.db_id))
    rd = json.loads(rds)
    vote.update_redis_dict(rd)

    return vote

def get_user_identifier() -> str:
    if current_user.is_anonymous:
        return 'anon:' + register_ip(request.remote_addr)
    else:
        return f"user:{current_user.id}"

def get_vote_object(channel_id: int, vote_id: int) -> Optional[Vote]:
    s = db_connect()
    vm = (s.query(models.VoteInfo).filter(models.VoteInfo.id == vote_id).
          one_or_none())
    if vm is None:
        return None
    v = Vote.from_model(vm)
    populate_vote(channel_id, v)
    return v

def send_vote_html(channel_id: int, vote_id: int, reopen: bool = False):
    """Render a vote in user-agnostic form (i.e. no voted-for annotations) and
    send the resulting HTML over the channel. This is called every time a vote
    is altered (votes added or removed, options added, config changed, etc.) in
    order to update clients' views.

    """
    s = db_connect()
    m = s.query(models.VoteInfo).filter(models.VoteInfo.id == vote_id).one()
    v = Vote.from_model(m)
    populate_vote(channel_id, v)
    # this is a dummy chapter object used only to get the channel ID
    dummy_chapter = { 'story': { 'channel_id': channel_id }}
    # in this case the public update will have vote totals hidden; we draw a
    # special update with totals shown, and send it only to the story author
    if v.votes_hidden and v.active:
        author_id = f"user:{m.post.story.author_id}"
        html = render_template('render_vote.html', vote=v, morph_swap=True,
                               is_author=True, chapter=dummy_chapter)
        websocket.pubsub.publish(author_id, html)
    html = render_template('render_vote.html', vote=v, morph_swap=True,
                           chapter=dummy_chapter)
    websocket.pubsub.publish(f'chan:{channel_id}', html)

@handle_message('add_vote')
@with_channel_auth()
def handle_add_vote(data) -> None:
    """Takes an info dictionary of the form:
    { 'channel': channel_id, 'vote': vote_id, 'option': option_id }

    """
    channel_id = data['channel']
    vote_id = data['vote']
    option_id = data['option']

    if not vote_is_active(channel_id, vote_id):
        return None

    uid = get_user_identifier()
    rv = db.redis_conn.fcall('add_vote', 2, f'channel_votes:{channel_id}',
                             'vote_info', vote_id, option_id, uid)
    if not rv:
        return

    conf = json.loads(db.redis_conn.hget('vote_info', str(vote_id)))
    print(conf)
    pl = { 'vote': vote_id, 'option': option_id, 'value': True,
           'clear': False }
    if not conf['multivote']:
        pl['clear'] = True
    m = Message(message_type='user-vote', data=pl, dest=uid)
    m.send()
    send_vote_html(int(channel_id), int(vote_id))

@handle_message('remove_vote')
@with_channel_auth()
def handle_remove_vote(data) -> None:
    assert db.redis_conn is not None

    channel_id = data['channel']
    vote_id = data['vote']
    option_id = data['option']

    if not vote_is_active(channel_id, vote_id):
        return None

    uid = get_user_identifier()

    rv = db.redis_conn.fcall('remove_vote', 2, f'channel_votes:{channel_id}',
                             'vote_info', vote_id, option_id, uid)
    if not rv:
        return

    m = Message(message_type='user-vote',
                data={ 'vote': vote_id, 'option': option_id, 'value': False,
                       'clear': False }, dest=uid)
    m.send()
    send_vote_html(int(channel_id), int(vote_id))

@handle_message('new_vote_entry')
@with_channel_auth()
def handle_new_vote_entry(data) -> None:
    assert db.redis_conn is not None

    channel_id = data['channel']
    vote_id = data['vote']
    # voteinfo = data['vote_info']
    option_text = data['option_text']

    if not vote_is_active(channel_id, vote_id):
        return None

    conf = json.loads(db.redis_conn.hget('vote_info', str(vote_id)))
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
    s.commit()

    # this picks up the db_id just set by Postgres
    option = VoteEntry.from_model(om)
    assert option.db_id is not None

    uid = get_user_identifier()
    rv = db.redis_conn.fcall('new_vote_entry', 2,
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
        s.delete(om)
        s.commit()
        return

    m = Message(message_type='user-vote',
                data={ 'vote': vote_id, 'option': option.db_id, 'value': True,
                       'clear': False }, dest=uid)
    m.send()
    send_vote_html(int(channel_id), int(vote_id))

def get_user_votes(vote_id: Union[int, str], user_id: Optional[str] = None) -> set[int]:
    """Takes a vote ID and a user identifier, which is either a string with
    "user:<userid>", or a hex IP hash. Returns a set of option IDs."""
    assert db.redis_conn is not None

    if user_id is None:
        user_id = get_user_identifier()
    user_id = str(user_id)

    rv = set()
    vis = db.redis_conn.hget('vote_info', str(vote_id))
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
def close_vote(channel_id: int, vote_id: int, set_close_time: bool = True,
               emit_client_event: bool = True, s: Optional[orm.Session] = None
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

    if not vote_is_active(channel_id, vote_id):
        return None

    if s is None:
        s = db_connect()
    vm = s.query(models.VoteInfo).filter(models.VoteInfo.id == vote_id).one()
    ve = Vote.from_model(vm)

    populate_vote(channel_id, ve)
    print(f"closing vote {ve}")

    channel_key = f"channel_votes:{channel_id}"
    removed = db.redis_conn.srem(channel_key, str(vote_id))
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
        v.votes.clear()
        for u in vl:
            if u.startswith('user:'):
                user_id = int(u[5:])
                um = models.UserVote(user_id=user_id)
            else:
                anon_id = u[5:]
                um = models.UserVote(anon_id=anon_id)
            v.votes.append(um)
    print("vm:", vm)
    s.commit()

    db.redis_conn.zrem('vote_close_times', f"{channel_id}:{ve.db_id}")
    db.redis_conn.hdel('vote_info', str(vote_id))

    if emit_client_event:
        websocket.pubsub.publish(f'chan:{channel_id}',
                                 json.dumps({ 'type': 'set-vote-open',
                                              'vote_id': vote_id,
                                              'open': False }))

def close_to_db():
    s = db.Session()
    vl = db.redis_conn.hgetall('vote_info')
    vd = decode_redis_dict(vl)
    for vid, vs in vd.items():
        vi = json.loads(vs)
        channel_id = vi['channel_id']
        close_vote(channel_id, int(vid), set_close_time=False,
                   emit_client_event=False, s=s)

# This can just call add_active_vote again, unset time_closed on the vote
# entry, and emit an event to the frontend
def open_vote(channel_id: int, vote_id: int) -> None:
    s = db_connect()
    vm = s.query(models.VoteInfo).where(models.VoteInfo.id == vote_id).one()
    channel_id = vm.post.story.channel_id
    vm.time_closed = None
    add_active_vote(vm, channel_id)
    s.commit()

    websocket.pubsub.publish(f'chan:{channel_id}',
                             json.dumps({ 'type': 'set-vote-open',
                                          'vote_id': vote_id, 'open': True }))

@handle_message('set_vote_active')
def set_vote_active(data) -> None:
    channel_id = int(data['channel'])
    story = get_story(channel_id)

    # TODO do we want to think about multiple authors?
    if story.author != current_user:
        return

    vote_id = int(data['vote'])
    set_active = data['active']
    now_active = vote_is_active(channel_id, vote_id)

    if now_active == set_active:
        return

    if not set_active:
        close_vote(channel_id, vote_id)
    else:
        open_vote(channel_id, vote_id)

@handle_message('set_option_killed')
def set_option_killed(data) -> None:
    assert db.redis_conn is not None

    channel_id = data['channel']
    story = get_story(channel_id)

    if story.author != current_user:
        return

    vote_id = data['vote']
    option_id = data['option']
    killed = '1' if data['killed'] else '0'
    kill_string = data.get('message', '')

    if not vote_is_active(channel_id, vote_id):
        return

    rv = db.redis_conn.fcall('set_option_killed', 2,
                             f'channel_votes:{channel_id}',
                             'vote_info', vote_id, option_id, killed,
                             kill_string)
    if not rv:
        return None

    send_vote_html(int(channel_id), int(vote_id))

@handle_message('set_vote_options')
def set_vote_options(data) -> None:
    # TODO factor out this authentication code
    channel_id = int(data['channel'])
    story = get_story(channel_id)
    vote_id = int(data['vote'])

    if story.author != current_user:
        return

    if not vote_is_active(channel_id, vote_id):
        return

    # TODO handle close time here as well
    print(data)
    multivote = data.get('multivote') == 'true'
    writein_allowed = data.get('writein_allowed') == 'true'
    votes_hidden = data.get('votes_hidden') == 'true'
    # possible keys are ['multivote', 'writein_allowed', 'votes_hidden',
    # 'close_time']

    vote = get_vote_object(channel_id, vote_id)

    # attempts to turn multivote off are ignored, since this could require
    # deleting user votes
    if multivote:
        vote.multivote = True

    vote.writein_allowed = writein_allowed
    vote.votes_hidden = votes_hidden

    rv = db.redis_conn.fcall('set_vote_config', 2,
                             f'channel_votes:{channel_id}', 'vote_info',
                             vote_id, json.dumps(vote.to_redis_dict()))
    if not rv:
        return None

    send_vote_html(channel_id, vote_id)

@handle_message('set_vote_close_time')
def set_vote_close_time(data) -> None:
    # TODO factor out this authentication code
    channel_id = int(data['channel'])
    story = get_story(channel_id)
    vote_id = int(data['vote'])

    if story.author != current_user:
        return

    if not vote_is_active(channel_id, vote_id):
        return

    # TODO handle close time here as well
    print(data)

    vote = get_vote_object(channel_id, vote_id)

    if data.get('clear'):
        vote.close_time = None
    else:
        try:
            num = int(data.get('value'))
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

    rv = db.redis_conn.fcall('set_vote_config', 2,
                             f'channel_votes:{channel_id}', 'vote_info',
                             vote_id, json.dumps(rd))
    if not rv:
        return None

    if vote.close_time is not None:
        db.redis_conn.zadd('vote_close_times',
                           { f"{channel_id}:{vote.db_id}":
                             int(vote.close_time.timestamp() * 1000) })
    else:
        db.redis_conn.zrem('vote_close_times', f"{channel_id}:{vote.db_id}")

    send_vote_html(channel_id, vote_id)
