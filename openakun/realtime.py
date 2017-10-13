#!python3

from . import pages
from .pages import models, socketio
from flask_socketio import join_room, emit
from flask_login import current_user
from flask import request
import datetime

def get_channel(channel_id):
    s = pages.db_connect()
    channel = (s.query(models.Channel).
               filter(models.Channel.id == channel_id).one())
    return channel

@socketio.on('connect')
def handle_connect():
    print("Got connection")
    pages.db_setup()

@socketio.on('join')
def handle_join(data):
    print("Got join", data)
    channel_id = data['channel']
    channel = get_channel(channel_id)
    if channel.private:
        return { 'success': False, 'error': 'Channel is private' }
    join_room(channel_id)
    return { 'success': True }

@socketio.on('chat_msg')
def handle_chat(data):
    print("Chat message", data, request.remote_addr)
    channel_id = data['channel']
    channel = get_channel(channel_id)
    if channel.private:
        return
    message = data['msg']
    username = 'anon' if current_user.is_anonymous else current_user.name
    emit('chat_msg',
         { 'username': username, 'text': message,
           'date': (datetime.datetime.now().timestamp() * 1000) },
         room=channel_id)
