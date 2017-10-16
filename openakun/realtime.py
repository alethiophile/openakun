#!python3

from . import pages
from .pages import models, socketio
from flask_socketio import join_room, emit
from flask_login import current_user
from flask import request
from functools import wraps
import datetime, hashlib

if pages.sentry is not None:
    @socketio.on_error_default
    def sentry_report_socketio(e):
        pages.sentry.before_request()
        pages.sentry.captureException()
        pages.sentry.client.context.clear()

def get_channel(channel_id):
    s = pages.db_connect()
    channel = (s.query(models.Channel).
               filter(models.Channel.id == channel_id).one())
    return channel

def with_channel_auth(err_val=None):
    def return_func(f):
        @wraps(f)
        def auth_wrapper(data):
            channel = get_channel(data['channel'])
            if channel.private:
                return err_val
            data['channel_obj'] = channel
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
    print([get_browser_msg(i) for i in bl])
    send_back_messages(bl, request.sid)
    return { 'success': True }

def send_back_messages(msgs, to):
    for i in msgs:
        mo = get_browser_msg(i)
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

def get_browser_msg(msg, admin=False):
    rv = { 'is_anon': (msg.user is None),
           'text': msg.text, 'date': msg.date.timestamp() * 1000,
           'id_token': msg.id_token, 'channel': msg.channel_id }
    if msg.user is not None:
        rv['username'] = msg.user.name
    if admin and msg.user is None:
        rv['anon_id'] = msg.anon_id
    return rv

message_cache_len = 60

def get_back_messages(cid):
    s = pages.db_connect()
    rv = (s.query(models.ChatMessage).
          filter(models.ChatMessage.channel_id == cid).
          order_by(models.ChatMessage.date.desc()).
          limit(message_cache_len).
          all())
    return list(reversed(rv))


@socketio.on('chat_msg')
@with_channel_auth()
def handle_chat(data):
    print("Chat message", data, request.remote_addr, current_user)
    channel_id = data['channel']
    channel = data['channel_obj']
    c_ts = datetime.datetime.now()
    if current_user.is_anonymous:
        hashval = register_ip(request.remote_addr)
    s = pages.db_connect()
    cm = models.ChatMessage(
        id_token=data['id_token'],
        channel=channel,
        date=c_ts,
        text=data['msg']
    )
    if current_user.is_anonymous:
        cm.anon_id = hashval
    else:
        cm.user = current_user
    s.add(cm)
    s.commit()
    mo = get_browser_msg(cm)
    emit('chat_msg', mo, room=channel_id)
