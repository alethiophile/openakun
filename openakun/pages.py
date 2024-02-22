#!python

from . import models, realtime, websocket
from .data import Vote, clean_html, BadHTMLError, ChapterHTMLText, Post
from .general import csrf_check, make_csrf, login_mgr, db_connect

from flask import (render_template, request, redirect, url_for, flash, abort,
                   jsonify, session, current_app, Blueprint)
from flask_login import login_user, current_user, logout_user, login_required
from werkzeug import Response
from sentry_sdk import push_scope, capture_message, capture_exception

import itsdangerous
from passlib.context import CryptContext

from datetime import datetime, timezone

from typing import Optional, Union

def make_hasher():
    pwd_context = CryptContext(
        schemes=['pbkdf2_sha256'],
        deprecated='auto',
    )
    return pwd_context

questing = Blueprint('questing', __name__)

@questing.app_template_global()
def include_raw(filename):
    return (current_app.jinja_loader.
            get_source(current_app.jinja_env, filename)[0])

@login_mgr.user_loader
def load_user(user_id: int) -> Optional[models.User]:
    s = db_connect()
    return (s.query(models.User).
            filter(models.User.id == int(user_id)).one_or_none())

def get_signer():
    return itsdangerous.TimestampSigner(current_app.config['SECRET_KEY'])

@questing.route('/')
def main():
    s = db_connect()
    stories = s.query(models.Story).limit(10).all()
    return render_template("main.html", stories=stories)

@questing.route('/login', methods=['GET', 'POST'])
@csrf_check
def login() -> Union[str, Response]:
    if request.method == 'POST':
        s = db_connect()
        hasher = make_hasher()
        user = (s.query(models.User).
                filter(models.User.name == request.form['user']).one_or_none())
        if user is not None and hasher.verify(request.form['pass'],
                                              user.password_hash):
            login_user(user)
            make_csrf(force=True)
            next_url = request.form.get('next', url_for('questing.main'))
            return redirect(next_url)
        else:
            flash("Login failed")
            return redirect(url_for('questing.login'))
    else:
        return render_template("login.html")

@questing.route('/logout', methods=['POST'])
@csrf_check
def logout() -> Response:
    if not current_user.is_anonymous:
        logout_user()
        make_csrf(force=True)
    return redirect(url_for('questing.main'))

def create_user(name: str, email: str, password: str) -> models.User:
    hasher = make_hasher()
    return models.User(
        name=name,
        email=email,
        password_hash=hasher.hash(password),
        joined_date=datetime.now(tz=timezone.utc)
    )

def add_user(name: str, email: str, password: str) -> models.User:
    s = db_connect()
    u = create_user(name, email, password)
    s.add(u)
    s.commit()
    return u

@questing.route('/signup', methods=['GET', 'POST'])
@csrf_check
def register() -> Union[str, Response]:
    if request.method == 'POST':
        s = db_connect()
        if request.form['pass1'] != request.form['pass2']:
            flash("Passwords did not match")
            return redirect(url_for('questing.register'))
        tu = (s.query(models.User).
              filter(models.User.name == request.form['user']).one_or_none())
        if tu is not None:
            flash("Username not available")
            return redirect(url_for('questing.register'))
        u = add_user(request.form['user'], request.form['email'],
                     request.form['pass1'])
        login_user(u)
        return redirect(url_for('questing.main'))
    else:
        return render_template("signup.html")

def add_story(title: str, desc: str, author: models.User) -> models.Story:
    s = db_connect()
    desc_clean = clean_html(desc)
    ns = models.Story(title=title, description=desc_clean, author=author)
    nc = models.Chapter(order_idx=0, story=ns, title='Chapter 1')
    chan = models.Channel()
    ns.channel = chan
    s.add(ns)
    s.add(nc)
    s.add(chan)
    s.commit()
    return ns

@questing.route('/new_story', methods=['GET', 'POST'])
@login_required
@csrf_check
def post_story() -> Union[str, Response]:
    if request.method == 'POST':
        try:
            ns = add_story(request.form['title'], request.form['description'],
                           current_user)
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
        return render_template("post_story.html")

@questing.route('/story/<int:story_id>')
def view_story(story_id) -> Response:
    s = db_connect()
    story = s.query(models.Story).filter(models.Story.id == story_id).one()
    return redirect(url_for('questing.view_chapter', story_id=story.id,
                            chapter_id=story.chapters[0].id))

@questing.app_template_global()
def prepare_post(p: models.Post, user_votes: bool = False) -> None:
    if getattr(p, 'prepared', False):
        return
    p.prepared = True
    p.rendered_date = (p.posted_date.astimezone(timezone.utc).
                       strftime("%b %d, %Y %I:%M %p UTC"))
    p.date_millis = (p.posted_date.timestamp() * 1000)
    if p.post_type == models.PostType.Vote:
        channel_id = p.story.channel_id
        p.vote = full_vote_info(channel_id, p.vote_info, user_votes)

def full_vote_info(channel_id: int, vm: models.VoteInfo,
                   user_votes: bool = False) -> Vote:
    uid = realtime.get_user_identifier() if user_votes else None
    v = Vote.from_model(vm, uid)
    realtime.populate_vote(channel_id, v)
    if user_votes and v.active:
        uv = realtime.get_user_votes(vm.id)
        for o in v.votes:
            o.user_voted = o.db_id in uv
    return v

@questing.route('/story/<int:story_id>/<int:chapter_id>')
def view_chapter(story_id: int, chapter_id: int) -> str:
    s = db_connect()
    chapter = (s.query(models.Chapter).
               filter(models.Chapter.id == chapter_id,
                      models.Chapter.story_id == story_id).
               one_or_none())
    chat_backlog = [i.to_browser_message() for i in
                    realtime.get_back_messages(chapter.story.channel_id)]
    if chapter is None:
        abort(404)
    is_author = chapter.story.author == current_user
    return render_template("view_chapter.html", chapter=chapter,
                           msgs=chat_backlog, is_author=is_author)

@questing.route('/vote/<int:vote_id>')
def view_vote(vote_id: int) -> str:
    s = db_connect()
    ve = (s.query(models.VoteInfo).filter(models.VoteInfo.id == vote_id).
          one_or_none())
    if ve is None:
        abort(404)
    channel_id = ve.post.chapter.story.channel_id
    chapter = ve.post.chapter
    is_author = chapter.story.author == current_user
    vote = full_vote_info(channel_id, ve, True)
    return render_template("render_vote.html", chapter=chapter, vote=vote,
                           is_author=is_author)

def create_post(c: models.Chapter, ptype: models.PostType, text: Optional[str],
                order_idx: Optional[int] = None) -> models.Post:
    s = db_connect()
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
        order_idx = (s.query(models.func.max(models.Post.order_idx).
                             label('max')).
                     filter(models.Post.chapter == c).one().max)
        if order_idx is None:
            order_idx = 0
        else:
            order_idx += 10
    np = models.Post(
        text=post_text,
        posted_date=datetime.now(tz=timezone.utc),
        post_type=ptype,
        chapter=c, story=c.story, order_idx=order_idx)
    s.add(np)
    s.commit()
    return np

def create_chapter(story: models.Story, title: str,
                   order_idx: Optional[int] = None) -> models.Chapter:
    s = db_connect()
    if order_idx is None:
        order_idx = (s.query(models.func.max(models.Chapter.order_idx).
                             label('max')).
                     filter(models.Chapter.story == story).one().max)
        if order_idx is None:
            order_idx = 0
        else:
            order_idx += 10
    nc = models.Chapter(
        title=title, story=story, order_idx=order_idx,
        is_appendix=False)
    s.add(nc)
    s.commit()
    return nc

@questing.route('/new_post', methods=['POST'])
@csrf_check
def new_post() -> Response:
    s = db_connect()
    print('new_post')
    data = request.json
    assert data is not None
    print(data)
    c = (s.query(models.Chapter).
         filter(models.Chapter.id == data['chapter_id']).one_or_none())
    if c is None:
        abort(404)
    if current_user != c.story.author:
        abort(403)
    if data['new_chapter']:
        if data['chapter_title'] == '':
            abort(400)
        nc = create_chapter(c.story, data['chapter_title'])
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
    s.commit()
    # emit the post after committing the session, so that clients don't see a
    # chapter that failed DB write
    if p.post_type == models.PostType.Vote:
        # vote_info = Vote.from_model(vote_model)
        realtime.add_active_vote(vote_model, c.story.channel_id)
    prepare_post(p, user_votes=False)
    text = render_template('render_post.html', p=p, htmx=True)
    websocket.pubsub.publish(f'chan:{channel_id}', text)
    return jsonify({ 'new_url': url_for('questing.view_chapter',
                                        story_id=p.story.id,
                                        chapter_id=p.chapter.id) })

@questing.route('/reopen_vote/<int:channel_id>/<int:vote_id>', methods=['POST'])
@csrf_check
def reopen_vote(channel_id: int, vote_id: int) -> str:
    # this just passes on to set_vote_active(), which handles authentication
    # and verification
    msg = { 'channel': channel_id, 'vote': vote_id, 'active': True }
    realtime.set_vote_active(msg)
    return ''

@questing.route('/user/<int:user_id>')
def user_profile(user_id: int) -> str:
    s = db_connect()
    u = (s.query(models.User).filter(models.User.id == user_id).one_or_none())
    if u is None:
        abort(404)
    sl = (s.query(models.Story).filter(models.Story.author == u).all())
    return render_template("user_profile.html", user=u, stories=sl)

@questing.app_template_global()
def get_dark_mode() -> bool:
    return session.get('dark_mode', False)

@questing.route('/settings', methods=['POST'])
def change_settings() -> str:
    dark_mode = int(request.form.get('dark_mode', 0))
    session['dark_mode'] = bool(dark_mode)
    return ''
