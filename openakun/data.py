#!python3

from __future__ import annotations

import attr, secrets, bleach
from datetime import datetime, timezone
from flask_socketio import emit

from . import models

from typing import Optional, Dict, Any, List

@attr.s(auto_attribs=True)
class ChatMessage:
    # we have two separate browser_token and server_token because browser_token
    # is used for communications between the browser and redis, and
    # server_token is used for communications between redis and postgres

    # the browser generates browser_token and sends it with the chat message;
    # every new message sent by the server contains the corresponding
    # browser_token, which the browser can use to verify that the message it
    # sent came back

    # meanwhile, server_token is generated on the server and goes into redis,
    # and is also saved in postgres; it's used to verify that messages from
    # redis are only copied to postgres once

    # if browser_token and server_token were identical, then a malicious client
    # could choose a known, old server_token and send that with a new message;
    # this message would pass the messages_seen check, and thus get sent to
    # other clients and stored in redis, but would not be stored in postgres
    # due to the conflict

    # this would effectively be a "disappearing message" that could be seen
    # only by clients present at the time, and could not be verified later or
    # audited, which breaks the security model (small and paltry a security
    # exploit though that is)
    server_token: str
    msg_text: str
    channel_id: int
    date: datetime
    browser_token: Optional[str] = None
    anon_id: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None

    def __attrs_post_init__(self):
        if (self.anon_id is None) == (self.user_id is None):
            raise ValueError("must provide exactly one of user_id or anon_id")
        if (self.user_id is None) != (self.user_name is None):
            raise ValueError("must provide both or neither user_id and "
                             "user_name")

    @classmethod
    def new(cls, msg_text: str, browser_token: str, channel_id: int,
            date: datetime = None, anon_id: Optional[str] = None,
            user_id: Optional[int] = None,
            user_name: Optional[str] = None) -> ChatMessage:
        serv_tok = secrets.token_urlsafe()
        if date is None:
            date = datetime.now(tz=timezone.utc)
        return cls(
            msg_text=msg_text, browser_token=browser_token,
            server_token=serv_tok, channel_id=channel_id, date=date,
            anon_id=anon_id, user_id=user_id, user_name=user_name)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> ChatMessage:
        d['date'] = datetime.fromisoformat(d['date'])
        return cls(**d)

    @classmethod
    def from_model(cls, m: models.ChatMessage) -> ChatMessage:
        if m.anon_id is not None:
            user_info: Dict[str, Any] = { 'anon_id': m.anon_id }
        else:
            user_info = { 'user_id': m.user_id, 'user_name': m.user.name }
        assert m.text is not None
        return cls(
            server_token=m.id_token,
            msg_text=m.text,
            channel_id=m.channel_id,
            date=m.date,
            **user_info
        )

    def to_model(self) -> models.ChatMessage:
        rv = models.ChatMessage(
            id_token=self.server_token,
            channel_id=self.channel_id,
            date=self.date,
            text=self.msg_text
        )
        if self.anon_id is not None:
            rv.anon_id = self.anon_id
        elif self.user_id is not None:
            rv.user_id = self.user_id
        return rv

    def to_browser_message(self, admin=False) -> Dict[str, Any]:
        rv = { 'is_anon': (self.user_id is None),
               'text': self.msg_text, 'date': self.date.timestamp() * 1000,
               'id_token': self.browser_token, 'channel': self.channel_id }
        if self.user_name is not None:
            rv['username'] = self.user_name
        if admin and self.anon_id is not None:
            rv['anon_id'] = self.anon_id
        return rv

    def to_dict(self) -> Dict[str, Any]:
        rv = attr.asdict(self)
        rv['date'] = rv['date'].isoformat()
        return rv

@attr.s(auto_attribs=True)
class VoteEntry:
    text: str
    killed: bool = False
    vote_count: Optional[int] = None
    killed_text: Optional[str] = None
    db_id: Optional[int] = None
    # this is a contextual member used only when it's clear which user is meant
    user_voted: Optional[bool] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> VoteEntry:
        return cls(**d)

    @classmethod
    def from_model(cls, m: models.VoteEntry) -> VoteEntry:
        return cls(
            text=m.vote_text,
            killed=m.killed,
            killed_text=m.killed_text,
            db_id=m.id)

    def to_dict(self) -> Dict[str, Any]:
        return attr.asdict(self)

    def create_model(self) -> models.VoteEntry:
        return models.VoteEntry(
            vote_text=self.text,
            killed=self.killed,
            killed_text=self.killed_text
        )

@attr.s(auto_attribs=True)
class Vote:
    question: str
    multivote: bool
    writein_allowed: bool
    votes_hidden: bool
    votes: List[VoteEntry]
    close_time: Optional[datetime] = None
    db_id: Optional[int] = None
    active: Optional[bool] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Vote:
        c = d.copy()
        vl = [VoteEntry.from_dict(i) for i in c['votes']]
        c['votes'] = vl
        return cls(**c)

    @classmethod
    def from_model(cls, m: models.VoteInfo) -> Vote:
        vl = [VoteEntry.from_model(i) for i in m.votes]
        return cls(
            question=m.vote_question,
            multivote=m.multivote,
            writein_allowed=m.writein_allowed,
            votes_hidden=m.votes_hidden,
            votes=vl,
            db_id=m.id)

    def to_dict(self) -> Dict[str, Any]:
        return attr.asdict(self)

    def create_model(self) -> models.VoteInfo:
        """This method creates model class objects representing the vote. It should be
        used only for votes that have not yet been entered into the database at
        all.

        """
        rv = models.VoteInfo(
            vote_question=self.question,
            multivote=self.multivote,
            writein_allowed=self.writein_allowed,
            votes_hidden=self.votes_hidden)
        for v in self.votes:
            vo = v.create_model()
            vo.vote_info = rv
        return rv

class BadHTMLError(Exception):
    def __init__(self, *args, good_html: str, bad_html: str, **kwargs) -> None:
        self.good_html = good_html
        self.bad_html = bad_html
        super().__init__(*args, **kwargs)

    def __repr__(self):
        return (f"BadHTMLError(good_html={repr(self.good_html)}, "
                f"bad_html={repr(self.bad_html)})")

class HTMLText(object):
    allowed_tags: Optional[List[str]]

    def __init__(self, html_data: str) -> None:
        self.dirty_html = html_data
        assert self.allowed_tags is not None
        self.clean_html = bleach.clean(html_data,
                                       tags=self.allowed_tags,
                                       attributes=self.allowed_attributes)

    def allowed_attributes(self, tag: str, name: str, value: str) -> bool:
        raise NotImplementedError()

    def __str__(self) -> str:
        return self.clean_html

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(clean_html={repr(self.clean_html)}, "
                f"dirty_html={repr(self.dirty_html)})")

class ChapterHTMLText(HTMLText):
    allowed_tags = ['a', 'b', 'br', 'em', 'i', 'li', 'ol', 'p', 's', 'strong',
                    'strike', 'ul']

    def allowed_attributes(self, tag: str, name: str, value: str) -> bool:
        if tag == 'a':
            if name == 'data-achieve': return True
            if name == 'class' and value == 'achieve-link': return True
        return False

def clean_html(html_in: str) -> str:
    html = ChapterHTMLText(html_in)
    if html.clean_html != html.dirty_html:
        raise BadHTMLError(good_html=html.clean_html, bad_html=html.dirty_html)
    return html.clean_html

@attr.s(auto_attribs=True)
class Post:
    text: Optional[str] = attr.ib()
    post_type: models.PostType
    posted_date: datetime = attr.Factory(lambda: datetime.now(tz=timezone.utc))
    order_idx: Optional[int] = None

    @text.validator
    def _cleck_clean_html(self, attrib, val):
        if val is None:
            return
        html = ChapterHTMLText(val)
        if html.clean_html != html.dirty_html:
            raise BadHTMLError(good_html=html.clean_html,
                               bad_html=html.dirty_html)

    @classmethod
    def from_dict(cls, inp_dict: Dict[str, Any]) -> Post:
        d = inp_dict.copy()
        # enum class gets stored as member name
        d['post_type'] = models.PostType[d['post_type']]
        if 'posted_date' in d:
            d['posted_date'] = datetime.fromisoformat(d['posted_date'])
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        rv = attr.asdict(self)
        rv['post_type'] = rv['post_type'].name
        rv['posted_date'] = rv['posted_date'].isoformat()
        return rv

    @classmethod
    def from_model(cls, m: models.Post) -> Post:
        return cls(
            text=m.text,
            posted_date=m.posted_date,
            order_idx=m.order_idx,
            post_type=m.post_type)

    def create_model(self) -> models.Post:
        d = { 'text': self.text,
              'posted_date': self.posted_date,
              'post_type': self.post_type }
        if self.order_idx is not None:
            d['order_idx'] = self.order_idx
        # we do it this way because we have to not pass order_idx at all if
        # it's None, so that the model's default triggers
        return models.Post(**d)

@attr.s(auto_attribs=True)
class Message:
    message_type: str
    data: Dict[str, Any]
    dest: Optional[str] = None

    def send(self, room: Optional[str] = None) -> None:
        if room is None:
            room = self.dest
        emit(self.message_type, self.data, room=room)
