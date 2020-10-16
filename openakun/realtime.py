#!python3

from __future__ import annotations

from . import pages
from .pages import models, socketio
from .data import ChatMessage
from flask_socketio import join_room, emit
from flask_login import current_user
from flask import request
from functools import wraps
from operator import attrgetter
from datetime import datetime, timezone
import hashlib, json
from sentry_sdk import push_scope, capture_exception

from typing import List

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
    hashval = hashlib.sha256(addr.encode()).hexdigest()
    s = pages.db_connect()
    q_num = (s.query(models.AddressIdentifier).
             filter(models.AddressIdentifier.hash == hashval).count())
    if (q_num == 0):
        ai = models.AddressIdentifier(hash=hashval, ip=addr)
        s.add(ai)
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
