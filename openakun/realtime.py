#!python3

from .pages import models, socketio
from flask_socketio import join_room, leave_room, emit
from flask_login import current_user
import datetime

@socketio.on('connect')
def handle_connect():
    print("Got connection")

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
    emit('chat_msg',
         { 'username': current_user.name, 'text': message,
           'date': (datetime.datetime.now(tz=datetime.timezone.utc).
                    timestamp() * 1000) },
         room=room)
