#!python3

from __future__ import annotations

from . import pages
from .pages import models, socketio
from .data import ChatMessage, Vote, VoteEntry, Message
from flask_socketio import join_room, emit
from flask_login import current_user
from flask import request
from functools import wraps
from operator import attrgetter
from datetime import datetime, timezone
import hashlib, json
from sentry_sdk import push_scope, capture_exception

from typing import List, Dict, Optional, Any

if pages.using_sentry:
    @socketio.on_error_default
    def sentry_report_socketio(e):
        with push_scope() as scope:
            scope.set_extra('event', request.event)
            capture_exception()

def get_channel(channel_id):
    s = pages.db_connect()
    channel = (s.query(models.Channel).
               filter(models.Channel.id == channel_id).one())
    return channel

# user_id can be string ID or 'anon'
def check_channel_auth(channel_id, user_id):
    auth_hkey = '{}:{}'.format(user_id, channel_id)
    cv = pages.redis_conn.hget('channel_auth', auth_hkey)
    if cv is None:
        c = get_channel(channel_id)
        if c.private:
            cv = '0'
        else:
            cv = '1'
        pages.redis_conn.hset('channel_auth', auth_hkey, cv)
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

@socketio.on('connect')
def handle_connect():
    print("Got connection")
    pages.db_setup()

@socketio.on('join')
@with_channel_auth({ 'success': False, 'error': 'Channel is private' })
def handle_join(data):
    print("Got join", data)
    channel_id = data['channel']
    join_room(channel_id)
    return { 'success': True }

@socketio.on('backlog')
@with_channel_auth({ 'success': False, 'error': 'Channel is private' })
def handle_backlog(data):
    print("Sending backlog for channel", data['channel'])
    bl = get_back_messages(data['channel'])
    send_back_messages(bl, request.sid)
    return { 'success': True }

def send_back_messages(msgs: List[ChatMessage], to: str) -> None:
    for i in msgs:
        mo = i.to_browser_message()
        socketio.emit('chat_msg', mo, room=to)

def register_ip(addr):
    # TODO make this use Redis and a periodic sweep, like chat messages
    hashval = hashlib.sha256(addr.encode()).hexdigest()
    s = pages.db_connect()
    q_num = (s.query(models.AddressIdentifier).
             filter(models.AddressIdentifier.hash == hashval).count())
    if (q_num == 0):
        ai = models.AddressIdentifier(hash=hashval, ip=addr)
        s.add(ai)
    s.commit()
    return hashval

message_cache_len = 60

def get_back_messages(cid: int) -> List[ChatMessage]:
    assert pages.redis_conn is not None
    rkey = f"channel_messages:{cid}"
    msgs = [ChatMessage.from_dict(json.loads(i.decode())) for i in
            pages.redis_conn.lrange(rkey, - message_cache_len, -1)]
    redis_msg_count = len(msgs)
    if redis_msg_count < message_cache_len:
        s = pages.db_connect()
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

@socketio.on('chat_msg')
@with_channel_auth()
def handle_chat(data) -> None:
    print("Chat message", data, request.remote_addr, current_user)
    if len(data['msg']) == 0:
        return

    assert pages.redis_conn is not None
    token = data['id_token']
    if pages.redis_conn.zscore('messages_seen', token) is not None:
        return
    c_ts = datetime.now(tz=timezone.utc)
    us_now = c_ts.timestamp() * 1000000
    pages.redis_conn.zadd('messages_seen', { token: us_now })

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
    pages.redis_conn.rpush(rkey, val)

    pages.redis_conn.sadd('all_channels', rkey)

    mo = msg.to_browser_message()
    emit('chat_msg', mo, room=channel_id)

def add_active_vote(vote: Vote, channel_id: int) -> None:
    """This function takes trusted input: it gets called when a new vote is
    created, and its parameters come from pages.new_post(), which verifies the
    data.

    """
    assert vote.db_id is not None

    channel_key = f"channel_votes:{channel_id}"
    pages.redis_conn.sadd(channel_key, vote.db_id)

    vote_key = f"vote_options:{vote.db_id}"
    for v in vote.votes:
        assert v.db_id is not None
        pages.redis_conn.sadd(vote_key, v.db_id)

    vote_opts = { k: v for k, v in vote.to_dict().items() if k in
                  ['multivote', 'writein_allowed', 'votes_hidden'] }
    opts_key = f"vote_config:{vote.db_id}"
    pages.redis_conn.set(opts_key, json.dumps(vote_opts))

def vote_is_active(channel_id: int, vote_id: int) -> bool:
    channel_key = f"channel_votes:{channel_id}"
    return pages.redis_conn.sismember(channel_key, vote_id)

def populate_vote(channel_id: int, vote: Vote) -> Vote:
    """Takes a vote, populates its options with the killed/killed_text/vote_count
    items and it with active. vote is populated in-place, return value is
    always the same object. This is called by the Web endpoints in order to
    render active votes in new loads.

    """
    if not vote_is_active(channel_id, vote.db_id):
        vote.active = False
        return vote

    vote.active = True

    for v in vote.votes:
        option_key = f"option_votes:{v.db_id}"
        v.vote_count = pages.redis_conn.scard(option_key)
        ks = pages.redis_conn.hget("options_killed", v.db_id)
        if ks is None:
            v.killed = False
            v.killed_text = None
        elif ks == '':
            v.killed = True
            v.killed_text = None
        else:
            v.killed = True
            v.killed_text = ks

    return vote

def get_vote_config(vote_id: int) -> Dict[str, bool]:
    opts_key = f"vote_config:{vote_id}"
    rv = pages.redis_conn.get(opts_key)
    assert rv is not None
    return json.loads(rv)

def verify_valid_option(channel_id: int, vote_id: int,
                        option_id: Optional[int]) -> bool:
    """This function verifies that 1. the given vote belongs to the given channel,
    2. the given option belongs to the given vote, 3. the given option has not
    been killed.

    If option_id is None, skips the latter two steps and only checks 1.

    """
    # no race conditions should apply here; channel_votes and vote_options are
    # write-once quantities, while options_killed is only read as a oneshot
    channel_key = f"channel_votes:{channel_id}"
    if not pages.redis_conn.sismember(channel_key, str(vote_id)):
        return False

    if option_id is None:
        return True

    vote_key = f"vote_options:{vote_id}"
    if not pages.redis_conn.sismember(vote_key, str(option_id)):
        return False

    if pages.redis_conn.hexists("options_killed", str(option_id)):
        return False

    return True

def get_user_identifier() -> str:
    if current_user.is_anonymous:
        return register_ip(request.remote_addr)
    else:
        return f"user:{current_user.id}"

def do_add_vote(vote_id: int, option_id: int,
                uid: str) -> List[Message]:
    """This function does the work of add_vote, including the somewhat tricky
    no-multivote logic. It trusts its input; all authentication is done by
    handle_add_vote. The given uid is a user ID as returned by
    get_user_identifier.

    The return value is a list of dicts which should (if appropriate) be
    emitted as option_vote_total messages. This function already takes account
    of the votes_hidden option, so no values are returned if that option is
    set.

    """

    # in the non-multivote case, we need to atomically 1. add the new vote, 2.
    # set the user_votes value to the new vote, 3. remove the old vote (from
    # the prior user_votes value); there's a potential for race conditions

    # the key is to put the atomic getset operation on user_votes between
    # adding the new vote and removing the old; this ensures that any option in
    # the getset value has already been voted for, and so removing it and
    # unsetting that vote is always a valid operation

    # really this is a silly concern, since it can only happen if a single user
    # is spamming vote messages at high rate, if I have problems with this I'll
    # just implement rate-limiting or something.

    # there is still a potential race condition where your existing vote is X
    # and you quickly switch to Y then back to X, where your vote ends up
    # getting deleted from both X and Y

    # I don't know if I actually care, but if so the answer is probably lua
    # scripting on the redis end

    # and all this is equally stupid because the only plausible situation in
    # which there is contention on this is if nginx is load-balancing multiple
    # socketios, and in that case one IP must always go to the same process and
    # so will never have contention

    opts = get_vote_config(vote_id)
    rv = []

    option_key = f"option_votes:{option_id}"
    res = pages.redis_conn.sadd(option_key, uid)

    if not opts['multivote']:
        uv_key = f"user_votes:{vote_id}:{uid}"
        old_vote_bytes = pages.redis_conn.getset(uv_key, option_id)
        old_option_id = (None if old_vote_bytes is None else
                         int(old_vote_bytes.decode()))
        if old_option_id == option_id:
            # in this case it's a repeat vote, so we don't need to do anything
            old_option_id = None

    if res == 0:
        # in this case, it was a duplicate vote message for what the user was
        # already voting for, so we don't need either to send a new totals
        # message, or to unvote the old vote if not multivote
        return []

    rv.append(Message(
        message_type='user_vote',
        data={ 'vote': vote_id, 'option': option_id, 'value': True },
        dest=request.sid))

    unvote_res = 0
    if not opts['multivote']:
        if old_option_id is not None:
            old_option_key = f"option_votes:{old_option_id}"
            unvote_res = pages.redis_conn.srem(old_option_key, uid)
            rv.append(Message(
                message_type='user_vote',
                data={ 'vote': vote_id, 'option': old_option_id,
                       'value': False },
                dest=request.sid))

    if not opts['votes_hidden']:
        if res > 0:
            t = pages.redis_conn.scard(option_key)
            msg = { 'vote': vote_id, 'option': option_id, 'vote_total': t }
            rv.append(Message(message_type='option_vote_total', data=msg))

        if unvote_res > 0:
            t = pages.redis_conn.scard(old_option_key)
            msg = { 'vote': vote_id, 'option': old_option_id, 'vote_total': t }
            rv.append(Message(message_type='option_vote_total', data=msg))

    return rv

@socketio.on('add_vote')
@with_channel_auth()
def handle_add_vote(data) -> None:
    """Takes an info dictionary of the form:
    { 'channel': channel_id, 'vote': vote_id, 'option': option_id }

    """
    channel_id = data['channel']
    vote_id = data['vote']
    option_id = data['option']

    if not verify_valid_option(channel_id, vote_id, option_id):
        return None

    uid = get_user_identifier()
    messages = do_add_vote(vote_id, option_id, uid)
    for m in messages:
        if m.dest is None:
            m.dest = channel_id
        m.send()
        # emit('option_vote_total', m, room=channel_id)

@socketio.on('remove_vote')
@with_channel_auth()
def handle_remove_vote(data) -> None:
    channel_id = data['channel']
    vote_id = data['vote']
    option_id = data['option']

    if not verify_valid_option(channel_id, vote_id, option_id):
        return None

    opts = get_vote_config(vote_id)
    uid = get_user_identifier()

    option_key = f"option_votes:{option_id}"
    res = pages.redis_conn.srem(option_key, uid)

    if res == 0:
        return None

    emit('user_vote',
         {'vote': vote_id, 'option': option_id, 'value': False },
         room=request.sid)

    if not opts['multivote']:
        uv_key = f"user_votes:{vote_id}:{uid}"
        pages.redis_conn.delete(uv_key)

    if not opts['votes_hidden'] and res > 0:
        t = pages.redis_conn.scard(option_key)
        msg = { 'vote': vote_id, 'option': option_id, 'vote_total': t }
        emit('option_vote_total', msg, room=channel_id)

@socketio.on('new_vote_entry')
@with_channel_auth()
def handle_new_vote_entry(data) -> None:
    channel_id = data['channel']
    vote_id = data['vote']
    voteinfo = data['vote_info']

    if not verify_valid_option(channel_id, vote_id, None):
        return None

    opts = get_vote_config(vote_id)
    if not opts['writein_allowed']:
        return None

    option = VoteEntry.from_dict(voteinfo)
    # the only thing we accept from the client is the option text
    option.killed = False
    option.killed_text = None
    option.vote_count = 1
    option.db_id = None

    m = option.create_model()
    m.vote_id = vote_id
    s = pages.db_connect()
    s.add(m)
    s.commit()

    # this picks up the db_id just set by Postgres
    option = VoteEntry.from_model(m)
    vote_key = f"vote_options:{vote_id}"
    pages.redis_conn.sadd(vote_key, option.db_id)

    uid = get_user_identifier()
    total_msgs = do_add_vote(vote_id, option.db_id, uid)

    new_entry_message = { 'vote': vote_id, 'vote_info': option.to_dict() }
    emit('vote_entry_added', new_entry_message, room=channel_id)
    for m in total_msgs:
        if m.dest is None:
            m.dest = channel_id
        m.send()

@socketio.on('get_my_votes')
def request_my_votes(data) -> None:
    vote_id = data['vote']

    vote_key = f'vote_options:{vote_id}'
    opts = pages.redis_conn.smembers(vote_key)

    uid = get_user_identifier()

    for o in opts:
        o = o.decode()
        opt_key = f'option_votes:{o}'
        print(f"{opt_key=}")
        if pages.redis_conn.sismember(opt_key, uid):
            emit('user_vote',
                 {'vote': vote_id, 'option': o, 'value': True },
                 room=request.sid)
