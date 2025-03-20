#!python3

from __future__ import annotations

import secrets, bleach, json
from datetime import datetime, timezone
from attrs import define, field, asdict
from sqlalchemy.ext.asyncio import AsyncSession

from . import models

from typing import Optional, Dict, Any, List

@define
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

    def __attrs_post_init__(self) -> None:
        if (self.anon_id is None) == (self.user_id is None):
            raise ValueError("must provide exactly one of user_id or anon_id")
        if (self.user_id is None) != (self.user_name is None):
            raise ValueError("must provide both or neither user_id and "
                             "user_name")

        self.channel_id = int(self.channel_id)
        if self.user_id is not None:
            self.user_id = int(self.user_id)

    @classmethod
    def new(cls, msg_text: str, browser_token: str, channel_id: int,
            date: Optional[datetime] = None, anon_id: Optional[str] = None,
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

    def to_browser_message(
            self, anon_username: str = 'anon', admin: bool = False
    ) -> Dict[str, Any]:
        rv = { 'is_anon': (self.user_id is None),
               'text': self.msg_text, 'date': self.date,
               'rendered_date': (self.date.astimezone(timezone.utc).
                                 strftime("%b %d, %Y %I:%M %p UTC")),
               'id_token': self.browser_token, 'channel': self.channel_id }
        if self.user_name is not None:
            rv['username'] = self.user_name
        if admin and self.anon_id is not None:
            rv['anon_id'] = self.anon_id
        if self.user_id is None:
            rv['username'] = anon_username
        return rv

    def to_dict(self) -> Dict[str, Any]:
        rv = asdict(self)
        rv['date'] = rv['date'].isoformat()
        return rv

@define
class VoteEntry:
    text: str
    killed: bool = False
    killed_text: Optional[str] = None
    vote_count: Optional[int] = None
    db_id: Optional[int] = None
    users_voted_for: Optional[list[str]] = None
    # this is a contextual member used only when it's clear which user is meant
    user_voted: Optional[bool] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> VoteEntry:
        return cls(**d)

    @classmethod
    def from_model(cls, m: models.VoteEntry,
                   uid: Optional[str] = None) -> VoteEntry:
        uvl = [f'user:{i.user_id}' if i.user_id else f'anon:{i.anon_id}'
               for i in m.votes]
        if uid is not None:
            user_voted = uid in uvl
        else:
            user_voted = None
        return cls(
            text=m.vote_text,
            killed=m.killed,
            killed_text=m.killed_text,
            db_id=m.id,
            users_voted_for=uvl,
            vote_count=len(uvl),
            user_voted=user_voted)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_redis_dict(self) -> Dict[str, Any]:
        return {
            'killed': self.killed,
            'killed_text': self.killed_text,
            'users_voted_for': self.users_voted_for,
        }

    def update_redis_dict(self, d: Dict[str, Any]) -> None:
        self.killed = d.get('killed', False)
        if self.killed:
            self.killed_text = d.get('killed_text')
        else:
            self.killed_text = None
        self.users_voted_for = list(set(d.get('users_voted_for', [])))
        self.vote_count = len(self.users_voted_for)

    def set_model_votes(self, em: models.VoteEntry) -> None:
        """Given a model, update the votes on it to match the current
        users_voted_for. This clears out the existing data in Postgres; make
        sure this is what you want.

        """
        if self.users_voted_for is None:
            return
        em.votes.clear()
        uvs = set(self.users_voted_for or [])
        for i in uvs:
            try:
                vm = models.UserVote(user_id=int(i))
            except ValueError:
                vm = models.UserVote(anon_id=i)
            em.votes.append(vm)

    def create_model(self) -> models.VoteEntry:
        em = models.VoteEntry(
            vote_text=self.text,
            killed=self.killed,
            killed_text=self.killed_text)
        if self.users_voted_for:
            self.set_model_votes(em)
        return em

@define
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
    def from_model(cls, m: models.VoteInfo, uid: Optional[str] = None) -> Vote:
        vl = [VoteEntry.from_model(i, uid) for i in m.votes]
        return cls(
            question=m.vote_question,
            multivote=m.multivote,
            writein_allowed=m.writein_allowed,
            votes_hidden=m.votes_hidden,
            close_time=m.time_closed,
            votes=vl,
            db_id=m.id)

    @classmethod
    async def from_model_dbload(
            cls, s: AsyncSession, vm: models.VoteInfo, uid: str | None = None
    ) -> Vote:
        await s.refresh(vm, ["votes"])
        for ve in vm.votes:
            await s.refresh(ve, ["votes"])
        return cls.from_model(vm, uid)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_redis_dict(self) -> Dict[str, Any]:
        vd = { str(i.db_id): i.to_redis_dict() for i in self.votes }
        return {
            'multivote': self.multivote,
            'writein_allowed': self.writein_allowed,
            'votes_hidden': self.votes_hidden,
            'votes': vd,
            'close_time': (self.close_time.isoformat() if self.close_time
                           else False),
        }

    def update_redis_dict(self, d: Dict[str, Any]) -> None:
        """This updates self in-place according to the passed-in Redis dict. A
        Redis dict contains only the data necessary to the
        performance-sensitive vote-counting code.

        """
        self.multivote = d.get('multivote', False)
        self.writein_allowed = d.get('writein_allowed', False)
        self.votes_hidden = d.get('votes_hidden', False)
        self.close_time = (datetime.fromisoformat(d['close_time'])
                           if d.get('close_time') else None)
        for vk, o in d['votes'].items():
            di = int(vk)
            for i in self.votes:
                if i.db_id == di:
                    i.update_redis_dict(o)
                    break

    def create_model(self) -> models.VoteInfo:
        """This method creates model class objects representing the vote. It
        should be used only for votes that have not yet been entered into the
        database at all.

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

class BadHTMLError(ValueError):
    def __init__(
            self, *args: Any, good_html: str, bad_html: str, **kwargs: Any
    ) -> None:
        self.good_html = good_html
        self.bad_html = bad_html
        super().__init__(*args, **kwargs)

    def __repr__(self) -> str:
        return (f"BadHTMLError(good_html={repr(self.good_html)}, "
                f"bad_html={repr(self.bad_html)})")

class HTMLText(object):
    allowed_tags: Optional[List[str]]

    def __init__(self, html_data: str, allow_mismatch: bool = False) -> None:
        self.dirty_html = html_data
        assert self.allowed_tags is not None
        self.clean_html = bleach.clean(html_data,
                                       tags=self.allowed_tags,
                                       attributes=self.allowed_attributes)
        if self.clean_html != self.dirty_html and not allow_mismatch:
            raise BadHTMLError(bad_html=self.dirty_html,
                               good_html=self.clean_html)

    @classmethod
    def from_user_input(cls, html_data: str):
        """for any special processing necessary for the from-user-input HTML;
        in this class, equivalent to just calling init"""
        return cls(html_data)

    def allowed_attributes(self, tag: str, name: str, value: str) -> bool:
        raise NotImplementedError()

    def output_html(self) -> str:
        """for any special processing before rendering"""
        return self.clean_html

    def __str__(self) -> str:
        return self.output_html()

    def __repr__(self) -> str:
        return (f"{type(self).__name__}(clean_html={repr(self.clean_html)}, "
                f"dirty_html={repr(self.dirty_html)})")

class PostHTMLText(HTMLText):
    allowed_tags = ['a', 'b', 'br', 'em', 'i', 'li', 'ol', 'p', 's', 'strong',
                    'strike', 'ul', 'u']

    def allowed_attributes(self, tag: str, name: str, value: str) -> bool:
        if tag == 'a':
            if name == 'data-achieve': return True
            if name == 'class' and value == 'achieve-link': return True
        return False

def clean_html(html_in: str) -> str:
    html = PostHTMLText(html_in)
    if html.clean_html != html.dirty_html:
        raise BadHTMLError(good_html=html.clean_html, bad_html=html.dirty_html)
    return html.clean_html

@define
class Post:
    text: Optional[PostHTMLText] = field(
        converter=lambda x: (x if isinstance(x, PostHTMLText)
                             else PostHTMLText(x)))
    post_type: models.PostType
    posted_date: datetime = field(
        factory=lambda: datetime.now(tz=timezone.utc))
    order_idx: Optional[int] = None

    @text.validator
    def _cleck_clean_html(self, attrib: Any, val: str | None) -> None:
        if val is None:
            return
        html = PostHTMLText(val)
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
        rv = asdict(self)
        rv['post_type'] = rv['post_type'].name
        rv['posted_date'] = rv['posted_date'].isoformat()
        return rv

    @classmethod
    def from_model(cls, m: models.Post) -> Post:
        return cls(
            text=m.text,
            posted_date=m.posted_date,
            order_idx=m.order_idx,
            post_type=models.PostType(m.post_type))

    def create_model(self) -> models.Post:
        d: dict[str, Any] = {
            'text': self.text,
            'posted_date': self.posted_date,
            'post_type': self.post_type }
        if self.order_idx is not None:
            d['order_idx'] = self.order_idx
        # we do it this way because we have to not pass order_idx at all if
        # it's None, so that the model's default triggers
        return models.Post(**d)

@define
class Message:
    message_type: str
    data: Dict[str, Any]
    dest: Optional[str] = None

    async def send(self, room: Optional[str] = None) -> None:
        from . import websocket
        if room is None:
            room = self.dest
        assert room is not None
        self.data['type'] = self.message_type
        await websocket.pubsub.publish(room, json.dumps(self.data))
