#!python

from . import models, realtime, websocket
from .data import Vote, clean_html, BadHTMLError, ChapterHTMLText, Post
from .general import csrf_check, make_csrf, login_mgr, db_connect

from quart import (render_template, request, redirect, url_for, flash, abort,
                   jsonify, session, current_app, Blueprint, make_response, g)
from .login import login_user, logout_user, login_required
from flask_htmx import HTMX
from quart import Response as QuartResponse
from werkzeug import Response as WerkzeugResponse
from sentry_sdk import push_scope, capture_message, capture_exception
from jinja2_fragments.quart import render_block
from sqlalchemy.sql.expression import select
from sqlalchemy.orm import selectinload

import itsdangerous, json, asyncio
from passlib.context import CryptContext

from datetime import datetime, timezone

from typing import Optional

ResponseType = str | QuartResponse | WerkzeugResponse

htmx = HTMX()

def make_hasher() -> CryptContext:
    pwd_context = CryptContext(
        schemes=['pbkdf2_sha256'],
        deprecated='auto',
    )
    return pwd_context

questing = Blueprint('questing', __name__)

@login_mgr.user_loader
async def load_user(user_id: int | str) -> models.User | None:
    async with db_connect() as s:
        return (await s.scalars(
            select(models.User).filter(models.User.id == int(user_id))
        )).one_or_none()

def get_signer() -> itsdangerous.TimestampSigner:
    return itsdangerous.TimestampSigner(current_app.config['SECRET_KEY'])

@questing.route('/')
async def main() -> ResponseType:
    async with db_connect() as s:
        stories = (await s.scalars(
            select(models.Story).limit(10))).all()
    return await render_template("main.html", stories=stories)

@questing.route('/login', methods=['GET', 'POST'])
@csrf_check
async def login() -> ResponseType:
    if request.method == 'POST':
        form = await request.form
        next_url = form.get('next', url_for('questing.main'))
        hasher = make_hasher()
        async with db_connect() as s:
            user = (await s.scalars(
                select(models.User).
                filter(models.User.name == form['user']))).one_or_none()
        if user is not None and hasher.verify(form['pass'],
                                              user.password_hash):
            login_user(user)
            make_csrf(force=True)
            return redirect(next_url)
        else:
            await flash("Login failed")
            return redirect(url_for('questing.login', next=next_url))
    else:
        return await render_template("login.html")

@questing.route('/logout', methods=['POST'])
@csrf_check
async def logout() -> ResponseType:
    if g.current_user is not None:
        logout_user()
        make_csrf(force=True)
    if htmx:
        return '', { "HX-Location": url_for('questing.main') }
    else:
        return redirect(url_for('questing.main'))

def create_user(name: str, email: str, password: str) -> models.User:
    hasher = make_hasher()
    return models.User(
        name=name,
        email=email,
        password_hash=hasher.hash(password),
        joined_date=datetime.now(tz=timezone.utc)
    )

async def add_user(name: str, email: str, password: str) -> models.User:
    async with db_connect() as s:
        async with s.begin():
            u = create_user(name, email, password)
            s.add(u)
    return u

@questing.route('/signup', methods=['GET', 'POST'])
@csrf_check
async def register() -> ResponseType:
    if request.method == 'POST':
        form = await request.form
        if form['pass1'] != form['pass2']:
            await flash("Passwords did not match")
            return redirect(url_for('questing.register'))
        async with db_connect() as s:
            tu = (await s.scalars(
                select(models.User).filter(models.User.name == form['user'])
            )).one_or_none()
        if tu is not None:
            await flash("Username not available")
            return redirect(url_for('questing.register'))
        u = await add_user(form['user'], form['email'],
                           form['pass1'])
        login_user(u)
        return redirect(url_for('questing.main'))
    else:
        return await render_template("signup.html")

async def add_story(title: str, desc: str, author: models.User) -> models.Story:
    desc_clean = clean_html(desc)
    ns = models.Story(title=title, description=desc_clean, author=author)
    nc = models.Chapter(order_idx=0, story=ns, title='Chapter 1')
    chan = models.Channel()
    ns.channel = chan
    async with db_connect() as s:
        async with s.begin():
            s.add(ns)
            s.add(nc)
            s.add(chan)
    return ns

@questing.route('/new_story', methods=['GET', 'POST'])
@login_required
@csrf_check
async def post_story() -> ResponseType:
    if request.method == 'POST':
        try:
            form = await request.form
            ns = await add_story(form['title'], form['description'],
                                 g.current_user)
        except BadHTMLError as e:
            if current_app.config['using_sentry']:
                with push_scope() as scope:
                    scope.set_extra('good_html', e.good_html)
                    scope.set_extra('bad_html', e.bad_html)
                    capture_message('HTML sanitization violation '
                                    '(description)')
            abort(400)
        return redirect(url_for('questing.view_story', story_id=ns.id))
    else:
        return await render_template("post_story.html")

@questing.route('/story/<int:story_id>')
async def view_story(story_id: int) -> ResponseType:
    async with db_connect() as s:
        story = (await s.scalars(
            select(models.Story).filter(models.Story.id == story_id))).one()
    return redirect(url_for('questing.view_chapter', story_id=story.id,
                            chapter_id=story.chapters[0].id))

@questing.app_template_global()
async def prepare_post(p: models.Post, user_votes: bool = False) -> None:
    if getattr(p, 'prepared', False):
        return
    p.prepared = True
    p.rendered_date = (p.posted_date.astimezone(timezone.utc).
                       strftime("%b %d, %Y %I:%M %p UTC"))
    p.date_millis = (p.posted_date.timestamp() * 1000)
    if p.post_type == models.PostType.Vote:
        channel_id = p.story.channel_id
        p.vote = full_vote_info(channel_id, p.vote_info, user_votes)

async def full_vote_info(channel_id: int, vm: models.VoteInfo,
                         user_votes: bool = False) -> Vote:
    uid = (await realtime.get_user_identifier()) if user_votes else None
    v = Vote.from_model(vm, uid)
    await realtime.populate_vote(channel_id, v)
    if user_votes and v.active:
        uv = await realtime.get_user_votes(vm.id)
        for o in v.votes:
            o.user_voted = o.db_id in uv
    return v

async def get_topics(story_id: int) -> list[models.Topic]:
    msgs = select(
        models.TopicMessage.topic_id,
        models.func.count(models.TopicMessage.id).label('num_msgs'),
        models.func.max(models.TopicMessage.post_date).label('latest_post')
    ).group_by(models.TopicMessage.topic_id).subquery()
    s = db_connect()
    topics = (await s.scalars(
        select(models.Topic).
        options(
            selectinload(models.Topic.poster),
            selectinload(models.Topic.messages)).
        outerjoin(msgs, models.Topic.id == msgs.c.topic_id).
        filter(models.Topic.story_id == story_id).
        order_by(msgs.c.latest_post.desc()))).all()
    return list(topics)

@questing.route('/story/<int:story_id>/<int:chapter_id>')
async def view_chapter(story_id: int, chapter_id: int) -> str:
    s = db_connect()
    chapter = (await s.scalars(
        select(models.Chapter).
        options(
            selectinload(models.Chapter.story).
            selectinload(models.Story.author),
            selectinload(models.Chapter.story).
            selectinload(models.Story.chapters).
            selectinload(models.Chapter.posts).
            selectinload(models.Post.vote_info).
            selectinload(models.VoteInfo.votes).
            selectinload(models.VoteEntry.votes)).
        filter(models.Chapter.id == chapter_id,
               models.Chapter.story_id == story_id)
    )).one_or_none()
    if chapter is None:
        abort(404)
    chat_backlog = [i.to_browser_message() for i in
                    await realtime.get_back_messages(chapter.story.channel_id)]
    is_author = chapter.story.author == g.current_user
    topics = await get_topics(story_id)
    if htmx and not htmx.history_restore_request:
        return await render_block("view_chapter.html", "content",
                                  chapter=chapter,
                                  msgs=chat_backlog, is_author=is_author,
                                  topics=topics, story=chapter.story)
    else:
        return await render_template("view_chapter.html", chapter=chapter,
                                     msgs=chat_backlog, is_author=is_author,
                                     topics=topics, story=chapter.story)

# this endpoint is used only when reopening a closed vote; it gets sent via the
# standard HTMX path (hx-get on the voteblock element in render_vote.html)
@questing.route('/vote/<int:vote_id>')
async def view_vote(vote_id: int) -> str:
    async with db_connect() as s:
        ve = (await s.scalars(
            select(models.VoteInfo).filter(models.VoteInfo.id == vote_id)
        )).one_or_none()
    if ve is None:
        abort(404)
    channel_id = ve.post.chapter.story.channel_id
    chapter = ve.post.chapter
    is_author = chapter.story.author == g.current_user
    vote = full_vote_info(channel_id, ve, True)
    return await render_template(
        "render_vote.html", chapter=chapter, vote=vote,
        is_author=is_author)

# used for updating the topic list over HTMX
@questing.route("/story/<int:story_id>/topics")
async def view_topic_list(story_id: int) -> str:
    async with db_connect() as s:
        story = (await s.scalars(
            select(models.Story).filter(models.Story.id == story_id)
        )).one_or_none()
    if story is None:
        abort(404)
    topics = await get_topics(story_id)
    return await render_template("topic_list.html", story=story, topics=topics)

async def create_post(c: models.Chapter, ptype: models.PostType, text: Optional[str],
                      order_idx: Optional[int] = None) -> models.Post:
    if ptype == models.PostType.Text:
        assert text is not None
        text_clean = ChapterHTMLText(text)
        if text_clean.clean_html != text_clean.dirty_html and \
           current_app.config['using_sentry']:
            with push_scope() as scope:
                scope.set_extra('bad_html', text_clean.dirty_html)
                scope.set_extra('good_html', text_clean.clean_html)
                capture_message('HTML sanitization violation (post)')
        post_text: Optional[str] = text_clean.clean_html
    elif text is None:
        post_text = None
    else:
        raise ValueError("can't pass text unless ptype is Text")
    # if no explicit order given, it's the last in the current
    # chapter, plus 10
    if order_idx is None:
        async with db_connect() as s:
            order_idx = (await s.scalars(
                select(models.func.max(models.Post.order_idx).
                       label('max')).
                filter(models.Post.chapter == c))).one()
        if order_idx is None:
            order_idx = 0
        else:
            order_idx += 10
    np = models.Post(
        text=post_text,
        posted_date=datetime.now(tz=timezone.utc),
        post_type=ptype,
        chapter=c, story=c.story, order_idx=order_idx)
    async with db_connect() as s:
        async with s.begin():
            s.add(np)
    return np

async def create_chapter(story: models.Story, title: str,
                         order_idx: Optional[int] = None) -> models.Chapter:
    if order_idx is None:
        async with db_connect() as s:
            order_idx = (await s.scalars(
                select(models.func.max(models.Chapter.order_idx).
                       label('max')).
                filter(models.Chapter.story == story)
            )).one()
        if order_idx is None:
            order_idx = 0
        else:
            order_idx += 10
    nc = models.Chapter(
        title=title, story=story, order_idx=order_idx,
        is_appendix=False)
    async with db_connect() as s:
        async with s.begin():
            s.add(nc)
    return nc

@questing.route('/new_post', methods=['POST'])
@csrf_check
async def new_post() -> ResponseType:
    s = db_connect()
    print('new_post')
    data = await request.json
    assert data is not None
    print(data)
    c = (await s.scalars(
        select(models.Chapter).
        filter(models.Chapter.id == data['chapter_id']))).one_or_none()
    if c is None:
        abort(404)
    if g.current_user != c.story.author:
        abort(403)
    if data['new_chapter']:
        if data['chapter_title'] == '':
            abort(400)
        nc = await create_chapter(c.story, data['chapter_title'])
    else:
        nc = c
    try:
        ptype = models.PostType[data['post_type']]
    except KeyError:
        abort(400)
    if ptype == models.PostType.Text:
        pt = data['post_text']
    else:
        pt = None
    # p = create_post(nc, ptype, pt)
    # this will throw BadHTMLError if HTML is bad
    try:
        post_info = Post(text=pt, post_type=ptype)
    except BadHTMLError as e:
        if current_app.config['using_sentry']:
            capture_exception(e)
        abort(400)
    p = post_info.create_model()
    p.chapter = nc
    p.story = nc.story
    s.add(p)
    if ptype == models.PostType.Vote:
        try:
            vote_info = Vote.from_dict(data['vote_data'])
        except Exception:
            abort(400)
        vote_model = vote_info.create_model()
        vote_model.post = p
        s.add(vote_model)
    channel_id = c.story.channel_id
    await s.commit()
    # emit the post after committing the session, so that clients don't see a
    # chapter that failed DB write
    if p.post_type == models.PostType.Vote:
        # vote_info = Vote.from_model(vote_model)
        await realtime.add_active_vote(vote_model, c.story.channel_id)
    await prepare_post(p, user_votes=False)
    text = await render_template('render_post.html', p=p, htmx=True,
                                 chapter=p.chapter)
    await websocket.pubsub.publish(f'chan:{channel_id}', text)
    return jsonify({ 'new_url': url_for('questing.view_chapter',
                                        story_id=p.story.id,
                                        chapter_id=p.chapter.id) })

@questing.route('/reopen_vote/<int:channel_id>/<int:vote_id>', methods=['POST'])
@csrf_check
async def reopen_vote(channel_id: int, vote_id: int) -> str:
    # this just passes on to set_vote_active(), which handles authentication
    # and verification
    msg = { 'channel': channel_id, 'vote': vote_id, 'active': True }
    realtime.set_vote_active(msg)
    return ''

@questing.route('/user/<int:user_id>')
async def user_profile(user_id: int) -> str:
    async with db_connect() as s:
        u = (await s.scalars(
            select(models.User).filter(models.User.id == user_id)
        )).one_or_none()
        if u is None:
            abort(404)
        sl = (await s.scalars(
            select(models.Story).filter(models.Story.author == u))).all()
    return await render_template("user_profile.html", user=u, stories=sl)

@questing.app_template_global()
def get_dark_mode() -> bool:
    return session.get('dark_mode', False)

@questing.route('/settings', methods=['POST'])
async def change_settings() -> ResponseType:
    dark_mode = int((await request.form).get('dark_mode', 0))
    session['dark_mode'] = bool(dark_mode)
    return '', { 'HX-Refresh': "true" }

@questing.route('/topic/<int:topic_id>')
async def view_topic(topic_id: int) -> str:
    htmx_partial = htmx and not htmx.history_restore_request
    async with db_connect() as s:
        topic = (await s.scalars(
            select(models.Topic).filter(models.Topic.id == topic_id)
        )).one_or_none()
    if topic is None:
        abort(404)
    if not htmx_partial:
        chat_backlog = [
            i.to_browser_message() for i in
            await realtime.get_back_messages(topic.story.channel_id)]
        topics = await get_topics(topic.story_id)
    # posts = (s.query(models.TopicMessage).filter(models.TopicMessage.topic_id ==
    #                                              topic_id).
    #          order_by(models.TopicMessage.post_date).all())
    if htmx_partial:
        return await render_block("view_topic.html", "content", topic=topic,
                                  story=topic.story)
    else:
        return await render_template("view_topic.html", topic=topic,
                                     story=topic.story, topics=topics,
                                     msgs=chat_backlog)

@questing.route('/new_topic', methods=['GET', 'POST'])
@csrf_check
async def new_topic() -> ResponseType:
    if request.method == 'GET':
        # send template
        story_id_s = request.args.get('story_id')
        story_id = None
        if story_id_s:
            try:
                story_id = int(story_id_s)
            except ValueError:
                abort(400)
        return await render_template("new_topic.html", story_id=story_id)
    elif request.method == 'POST':
        data = await request.json
        if data is None:
            abort(400)
        assert data is not None
        if 'story_id' in data:
            story_id = data['story_id']
            async with db_connect() as s:
                story = (await s.scalars(
                    select(models.Story).filter(models.Story.id == story_id)
                )).one_or_none()
            if story is None:
                abort(400)
        else:
            story = None
        # TODO check permissions/bans
        title = data['title']

        t = models.Topic(
            title=title,
            post_date=datetime.now(tz=timezone.utc))
        t.poster = g.current_user
        t.story = story
        async with db_connect() as s:
            async with s.begin():
                s.add(t)

        if story is not None:
            assert story_id is not None
            text = await view_topic_list(story_id)
            await websocket.pubsub.publish(f'chan:{story.channel_id}', text)

        # since this is going straight to HTMX, we just return the text that
        # view_topic would along with the appropriate URL header
        res = await make_response(view_topic(t.id))
        res.headers['HX-Push-Url'] = url_for('questing.view_topic',
                                             topic_id=t.id)
        return res
    # control never reaches here, since we assume the request method is either
    # GET or POST; this is just to satisfy mypy
    abort(400)

@questing.route('/new_topic_post', methods=['POST'])
@csrf_check
async def new_topic_post() -> ResponseType:
    data = await request.json
    if data is None:
        abort(400)
    topic_id = data['topic_id']
    async with db_connect() as s:
        topic = (await s.scalars(
            select(models.Topic).filter(models.Topic.id == topic_id)
        )).one_or_none()
    if topic is None:
        abort(400)

    try:
        text = clean_html(data['text'])
    except Exception:
        abort(400)

    message = models.TopicMessage(
        topic_id=topic_id,
        post_date=datetime.now(tz=timezone.utc),
        text=text)
    message.poster = g.current_user

    async with db_connect() as s:
        async with s.begin():
            s.add(message)

    if topic.story is not None:
        story_id = topic.story_id
        text = await view_topic_list(story_id)
        await websocket.pubsub.publish(f'chan:{topic.story.channel_id}', text)

    return redirect(url_for('questing.view_topic', topic_id=topic_id))
