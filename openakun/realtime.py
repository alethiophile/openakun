#!python3

from __future__ import annotations

from . import models
from .general import db, db_connect, db_setup
from .data import ChatMessage, Vote, VoteEntry, Message
from flask_socketio import join_room, emit, SocketIO
from flask_login import current_user
from flask import request, render_template
from functools import wraps
from operator import attrgetter
from datetime import datetime, timezone
import hashlib, json

from typing import List, Dict, Optional, Any, Union

socketio = SocketIO()

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

@socketio.on('connect')
def handle_connect():
    print("Got connection")
    db_setup()

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
        if mo['is_anon']:
            mo['username'] = 'anon'
        html = render_template('render_chatmsg.html', c=mo)
        socketio.emit('chat_msg', { 'html': html }, room=to)

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

@socketio.on('chat_msg')
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
    html = render_template('render_chatmsg.html', c=mo)
    emit('chat_msg', { 'html': html }, room=channel_id)

def add_active_vote(vote: Vote, channel_id: int) -> None:
    """This function takes trusted input: it gets called when a new vote is
    created, and its parameters come from pages.new_post(), which verifies the
    data.

    """
    assert vote.db_id is not None

    channel_key = f"channel_votes:{channel_id}"
    db.redis_conn.sadd(channel_key, vote.db_id)

    vote_key = f"vote_options:{vote.db_id}"
    for v in vote.votes:
        assert v.db_id is not None
        db.redis_conn.sadd(vote_key, v.db_id)

    vote_opts = { k: v for k, v in vote.to_dict().items() if k in
                  ['multivote', 'writein_allowed', 'votes_hidden'] }
    opts_key = f"vote_config:{vote.db_id}"
    db.redis_conn.set(opts_key, json.dumps(vote_opts))

def vote_is_active(channel_id: int, vote_id: int) -> bool:
    channel_key = f"channel_votes:{channel_id}"
    return db.redis_conn.sismember(channel_key, vote_id)

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

    vote_config = get_vote_config(vote.db_id)
    vote.multivote = vote_config['multivote']
    vote.writein_allowed = vote_config['writein_allowed']
    vote.votes_hidden = vote_config['votes_hidden']
    vote.close_time = vote_config.get('close_time')

    for v in vote.votes:
        option_key = f"option_votes:{v.db_id}"
        v.vote_count = db.redis_conn.scard(option_key)
        ks = db.redis_conn.hget("options_killed", v.db_id)
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

def get_vote_config(vote_id: int) -> Dict[str, Any]:
    opts_key = f"vote_config:{vote_id}"
    rv = db.redis_conn.get(opts_key)
    assert rv is not None
    return json.loads(rv)

def verify_valid_option(channel_id: int, vote_id: int,
                        option_id: Optional[int],
                        allow_killed: bool = False) -> bool:
    """This function verifies that 1. the given vote belongs to the given channel,
    2. the given option belongs to the given vote, 3. the given option has not
    been killed.

    If option_id is None, skips the latter two steps and only checks 1. If
    allow_killed is true, ignore the kill status.

    """
    # no race conditions should apply here; channel_votes and vote_options are
    # write-once quantities, while options_killed is only read as a oneshot
    channel_key = f"channel_votes:{channel_id}"
    if not db.redis_conn.sismember(channel_key, str(vote_id)):
        return False

    if option_id is None:
        return True

    vote_key = f"vote_options:{vote_id}"
    if not db.redis_conn.sismember(vote_key, str(option_id)):
        return False

    if (db.redis_conn.hexists("options_killed", str(option_id))
        and not allow_killed):
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
    res = db.redis_conn.sadd(option_key, uid)

    if not opts['multivote']:
        uv_key = f"user_votes:{vote_id}:{uid}"
        old_vote_bytes = db.redis_conn.getset(uv_key, option_id)
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
            unvote_res = db.redis_conn.srem(old_option_key, uid)
            rv.append(Message(
                message_type='user_vote',
                data={ 'vote': vote_id, 'option': old_option_id,
                       'value': False },
                dest=request.sid))

    return rv

def send_vote_html(channel_id: int, vote_id: int):
    s = db_connect()
    m = s.query(models.VoteInfo).filter(models.VoteInfo.id == vote_id).one()
    v = Vote.from_model(m)
    populate_vote(channel_id, v)
    html = render_template('render_vote.html', vote=v)
    msg = { 'vote': v.db_id, 'html': html }
    emit('rendered_vote', msg, room=str(channel_id))

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
    send_vote_html(int(channel_id), int(vote_id))

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
    res = db.redis_conn.srem(option_key, uid)

    if res == 0:
        return None

    emit('user_vote',
         {'vote': vote_id, 'option': option_id, 'value': False },
         room=request.sid)

    if not opts['multivote']:
        uv_key = f"user_votes:{vote_id}:{uid}"
        db.redis_conn.delete(uv_key)

    send_vote_html(int(channel_id), int(vote_id))

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

    option.text = option.text.strip()
    if not option.text:
        return None

    m = option.create_model()
    m.vote_id = vote_id
    s = db_connect()
    s.add(m)
    s.commit()

    # this picks up the db_id just set by Postgres
    option = VoteEntry.from_model(m)
    vote_key = f"vote_options:{vote_id}"
    db.redis_conn.sadd(vote_key, option.db_id)

    uid = get_user_identifier()
    total_msgs = do_add_vote(vote_id, option.db_id, uid)

    for m in total_msgs:
        if m.dest is None:
            m.dest = channel_id
        m.send()
    send_vote_html(int(channel_id), int(vote_id))

def get_user_votes(vote_id: Union[int, str], user_id: str = None) -> set[int]:
    """Takes a vote ID and a user identifier, which is either a string with
    "user:<userid>", or a hex IP hash. Returns a set of option IDs."""
    if user_id is None:
        user_id = get_user_identifier()
    vote_key = f'vote_options:{vote_id}'

    opts = db.redis_conn.smembers(vote_key)

    rv = set()
    for o in opts:
        o = o.decode()
        opt_key = f'option_votes:{o}'
        if db.redis_conn.sismember(opt_key, user_id):
            rv.add(int(o))

    return rv

@socketio.on('get_my_votes')
def request_my_votes(data) -> None:
    vote_id = data['vote']

    votes = get_user_votes(vote_id)
    for v in votes:
        emit('user_vote', { 'vote': vote_id, 'option': v, 'value': True })

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
def close_vote(channel_id: int, vote_id: int) -> None:
    """This function trusts its input: caller must verify that the given vote is
    open on the given channel.

    """
    s = db_connect()
    vm = s.query(models.VoteInfo).filter(models.VoteInfo.id == vote_id).one()
    ve = Vote.from_model(vm)

    populate_vote(channel_id, ve)

    # these parameters don't matter when the vote is closed but they're
    # preserved for if it opens again
    vm.multivote = ve.multivote
    vm.writein_allowed = ve.writein_allowed
    vm.votes_hidden = ve.votes_hidden

    # for closed votes, time_closed represents the actual time of closure, so
    # just set it to the current time
    vm.time_closed = datetime.now(tz=timezone.utc)

    ed = { i.db_id: i for i in ve.votes }
    for v in vm.votes:
        v.killed = ed[v.id].killed
        v.killed_text = ed[v.id].killed_text

        opt_key = f"option_votes:{v.db_id}"
        vl = [i.decode() for i in db.redis_conn.smembers(opt_key)]
        v.votes.clear()
        for u in vl:
            if u.startswith('user:'):
                user_id = int(u[5:])
                um = models.UserVote(user_id=user_id)
            else:
                anon_id = u
                um = models.UserVote(anon_id=anon_id)
            v.votes.append(um)
    s.commit()

    db.redis_conn.delete(f"vote_config:{vote_id}")
    db.redis_conn.delete(f"vote_options:{vote_id}")
    for v in vm.votes:
        db.redis_conn.delete(f"option_votes:{v.id}")
        db.redis_conn.hdel("options_killed", v.id)
    db.redis_conn.srem(f"channel_votes:{channel_id}", str(vote_id))

# This can just call add_active_vote again, unset time_closed on the vote
# entry, and emit an event to the fronte
def open_vote(channel_id: int, vote_id: int) -> None:
    pass

@socketio.on('set_vote_active')
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

@socketio.on('set_option_killed')
def set_option_killed(data) -> None:
    channel_id = data['channel']
    story = get_story(channel_id)

    if story.author != current_user:
        return

    vote_id = data['vote']
    option_id = data['option']
    killed = data['killed']
    kill_string = data.get('message', '')

    if not verify_valid_option(channel_id, vote_id, option_id,
                               allow_killed=True):
        return

    # Kill status is tracked by the options_killed hash. Keys in the hash are
    # numeric option IDs. If a key exists in the hash, the option has been
    # killed. The value may be an empty string, signifying no reason given, or
    # else a string describing the reason.
    if not killed:
        db.redis_conn.hdel("options_killed", str(option_id))
    else:
        db.redis_conn.hset("options_killed", str(option_id), kill_string)
    send_vote_html(int(channel_id), int(vote_id))

@socketio.on('set_vote_options')
def set_vote_options(data) -> None:
    # TODO factor out this authentication code
    channel_id = data['channel']
    story = get_story(channel_id)

    if story.author != current_user:
        return

    # possible keys are ['multivote', 'writein_allowed', 'votes_hidden',
    # 'close_time']
