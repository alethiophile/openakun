#!python3

from . import pages
from .pages import models, socketio
from flask_socketio import join_room, emit
from flask_login import current_user
import datetime

@socketio.on('connect')
def handle_connect():
    print("Got connection")
    pages.db_setup()

@socketio.on('join')
def handle_join(data):
    print("Got join", data)
    room = data['channel']
    join_room(room)

@socketio.on('chat_msg')
def handle_chat(data):
    print("Chat message", data)
    room = data['channel']
    message = data['msg']
    username = 'anon' if current_user.is_anonymous else current_user.name
    emit('chat_msg',
         { 'username': username, 'text': message,
           'date': (datetime.datetime.now().timestamp() * 1000) },
         room=room)
