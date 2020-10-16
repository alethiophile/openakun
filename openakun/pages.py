#!python

from . import models
from .data import Vote, clean_html, BadHTMLError, ChapterHTMLText

from flask import (Flask, render_template, request, redirect, url_for, g,
                   flash, abort, jsonify, session)
from flask_login import (LoginManager, login_user, current_user, logout_user,
                         login_required)
from werkzeug import Response
from werkzeug.middleware.proxy_fix import ProxyFix
from jinja2 import Markup
from flask_socketio import SocketIO
import sentry_sdk
from sentry_sdk import push_scope, capture_message
from sentry_sdk.integrations.flask import FlaskIntegration

import itsdangerous, redis
from passlib.context import CryptContext

import configparser, os, secrets, re, click, json
from datetime import datetime, timezone
from functools import wraps
from base64 import b64encode

from typing import Callable, Optional, Union

pwd_context = CryptContext(
    schemes=['pbkdf2_sha256'],
    deprecated='auto',
)

config = configparser.ConfigParser()
if os.environ.get('OPENAKUN_TESTING') == '1':
    config.read_dict(os.openakun_test_config)  # this is a terrible hack
else:
    config_fn = os.environ.get("OPENAKUN_CONFIG", 'openakun.cfg')
    rv = config.read(config_fn)
    if len(rv) == 0:
        raise RuntimeError("Couldn't find config file")

using_sentry = False
if 'sentry_dsn' in config['openakun']:
    using_sentry = True
    sentry_sdk.init(
        dsn=config['openakun']['sentry_dsn'],
        send_default_pii=True,
        integrations=[FlaskIntegration()]
    )

login_mgr = LoginManager()
app = Flask('openakun')

if config.getboolean('openakun', 'proxy_fix', fallback=False):
    print("adding ProxyFix")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

class ConfigError(Exception):
    pass

if ('secret_key' not in config['openakun'] or
    len(config['openakun']['secret_key']) == 0):  # noqa: E129
    raise ConfigError("Secret key not provided")

app.config['SECRET_KEY'] = config['openakun']['secret_key']
login_mgr.init_app(app)
login_mgr.login_view = 'login'
site_origin = config.get('openakun', 'main_origin', fallback=None)
ol = [site_origin] if site_origin is not None else None
socketio = SocketIO(app, cors_allowed_origins=ol,
                    logger=app.config['DEBUG'],
                    engineio_logger=app.config['DEBUG'])

app.jinja_env.add_extension('jinja2.ext.do')

# Import needs to be here, since it imports variables from pages.py itself.
# Importing alone has all the side effects needed.
from . import realtime  # noqa

def jinja_global(f):
    app.jinja_env.globals[f.__name__] = f
    return f

app.jinja_env.globals['models'] = models

@jinja_global
def include_raw(filename):
    return Markup(app.jinja_loader.get_source(app.jinja_env, filename)[0])

db_engine = None
Session = None
redis_conn = None

redis_url_re = re.compile(r"^redis://(?P<hostname>[a-zA-Z1-9.-]+)?"
                          r"(:(?P<port>\d+))?"
                          r"(/(?P<db>\d+))?$")

def parse_redis_url(url):
    o = redis_url_re.match(url)
    if o is None:
        raise ConfigError("Couldn't parse Redis url '{}'".format(url))
    rv = {}
    rv['host'] = o.group('hostname') or '127.0.0.1'
    rv['port'] = int(o.group('port') or '6379')
    rv['db'] = int(o.group('db') or '0')
    return rv

@app.before_first_request
def db_setup() -> None:
    global db_engine, Session, redis_conn
    if db_engine is None:
        db_engine = models.create_engine(config['openakun']['database_url'],
                                         echo=config.getboolean('openakun',
                                                                'echo_sql'))
        Session = models.sessionmaker(bind=db_engine)
    if redis_conn is None:
        redis_info = parse_redis_url(config['openakun']['redis_url'])
        redis_conn = redis.StrictRedis(**redis_info)

@app.before_request
def make_csrf(force: bool = False) -> None:
    if force or '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe()

@jinja_global
def get_script_nonce() -> str:
    if not hasattr(g, 'script_nonce'):
        # this has to be real base64, per the spec, not urlencoded; thus
        # token_urlsafe won't work
        g.script_nonce = b64encode(secrets.token_bytes()).decode()
    return g.script_nonce

@app.after_request
def add_csp(resp: Response) -> Response:
    nv = get_script_nonce()
    report_only = config['openakun']['csp_level'] == 'report'
    header_name = ('Content-Security-Policy-Report-Only' if report_only else
                   'Content-Security-Policy')
    hval = f"script-src 'nonce-{nv}' 'unsafe-eval'"
    # TODO report even in enforcing mode
    if report_only:
        hval += f"; report-uri {url_for('csp_report')}"
    resp.headers[header_name] = hval
    return resp

@app.route('/csp_violation_report', methods=['POST'])
def csp_report() -> str:
    # TODO log this
    print(request.data)
    if using_sentry:
        with push_scope() as scope:
            scope.set_extra('request', request.get_json())
            capture_message("CSP violation")
    return ''

def csrf_check(view: Callable) -> Callable:
    @wraps(view)
    def csrf_wrapper(*args, **kwargs):
        if request.method == 'POST':
            data = request.json
            tok = (data.get('_csrf_token', '') if data else
                   request.form.get('_csrf_token', ''))
            # constant-time compare operation
            if not secrets.compare_digest(tok,
                                          session['_csrf_token']):
                abort(400)
        return view(*args, **kwargs)
    return csrf_wrapper

@login_mgr.user_loader
def load_user(user_id: int) -> Optional[models.User]:
    s = db_connect()
    return (s.query(models.User).
            filter(models.User.id == int(user_id)).one_or_none())

def db_connect():
    if not hasattr(g, 'db_session'):
        g.db_session = Session()
    return g.db_session

def get_signer():
    return itsdangerous.TimestampSigner(app.config['SECRET_KEY'])

@app.route('/')
def main():
    s = db_connect()
    stories = s.query(models.Story).limit(10).all()
    return render_template("main.html", stories=stories)

@app.route('/login', methods=['GET', 'POST'])
@csrf_check
def login() -> Union[str, Response]:
    if request.method == 'POST':
        s = db_connect()
        user = (s.query(models.User).
                filter(models.User.name == request.form['user']).one_or_none())
        if user is not None and pwd_context.verify(request.form['pass'],
                                                   user.password_hash):
            login_user(user)
            make_csrf(force=True)
            next_url = request.form.get('next', url_for('main'))
            return redirect(next_url)
        else:
            flash("Login failed")
            return redirect(url_for('login'))
    else:
        return render_template("login.html")

@app.route('/logout', methods=['POST'])
@csrf_check
def logout() -> Response:
    if not current_user.is_anonymous:
        logout_user()
        make_csrf(force=True)
    return redirect(url_for('main'))

def create_user(name: str, email: str, password: str) -> models.User:
    return models.User(
        name=name,
        email=email,
        password_hash=pwd_context.hash(password),
        joined_date=datetime.now(tz=timezone.utc)
    )

def add_user(name: str, email: str, password: str) -> models.User:
    s = db_connect()
    u = create_user(name, email, password)
    s.add(u)
    s.commit()
    return u

@app.route('/signup', methods=['GET', 'POST'])
@csrf_check
def register() -> Union[str, Response]:
    if request.method == 'POST':
        s = db_connect()
        if request.form['pass1'] != request.form['pass2']:
            flash("Passwords did not match")
            return redirect(url_for('register'))
        tu = (s.query(models.User).
              filter(models.User.name == request.form['user']).one_or_none())
        if tu is not None:
            flash("Username not available")
            return redirect(url_for('register'))
        u = add_user(request.form['user'], request.form['email'],
                     request.form['pass1'])
        login_user(u)
        return redirect(url_for('main'))
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

@app.route('/new_story', methods=['GET', 'POST'])
@login_required
@csrf_check
def post_story() -> Union[str, Response]:
    if request.method == 'POST':
        try:
            ns = add_story(request.form['title'], request.form['description'],
                           current_user)
        except BadHTMLError as e:
            if using_sentry:
                with push_scope() as scope:
                    scope.set_extra('good_html', e.good_html)
                    scope.set_extra('bad_html', e.bad_html)
                    capture_message('HTML sanitization violation '
                                    '(description)')
            # if app.config['DEBUG']:
            #     raise
            # else:
            abort(400)
        return redirect(url_for('view_story', story_id=ns.id))
    else:
        return render_template("post_story.html")

@app.route('/story/<int:story_id>')
def view_story(story_id) -> Response:
    s = db_connect()
    story = s.query(models.Story).filter(models.Story.id == story_id).one()
    return redirect(url_for('view_chapter', story_id=story.id,
                            chapter_id=story.chapters[0].id))

@jinja_global
def prepare_post(p: models.Post) -> None:
    p.rendered_date = (p.posted_date.astimezone(timezone.utc).
                       strftime("%b %d, %Y %I:%M %p UTC"))
    p.date_millis = (p.posted_date.timestamp() * 1000)
    if p.post_type == models.PostType.Vote:
        p.vote_info_json = json.dumps(Vote.from_model(p.vote_info).to_dict())

@app.route('/story/<int:story_id>/<int:chapter_id>')
def view_chapter(story_id: int, chapter_id: int) -> str:
    s = db_connect()
    chapter = (s.query(models.Chapter).
               filter(models.Chapter.id == chapter_id,
                      models.Chapter.story_id == story_id).
               one_or_none())
    if chapter is None:
        abort(404)
    return render_template("view_chapter.html", chapter=chapter, server=True)

def create_post(c: models.Chapter, ptype: models.PostType, text: Optional[str],
                order_idx: Optional[int] = None) -> models.Post:
    s = db_connect()
    if ptype == models.PostType.Text:
        assert text is not None
        text_clean = ChapterHTMLText(text)
        if text_clean.clean_html != text_clean.dirty_html and using_sentry:
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

@app.route('/new_post', methods=['POST'])
@csrf_check
def new_post() -> Response:
    s = db_connect()
    print('new_post')
    data = request.json
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
    p = create_post(nc, ptype, pt)
    if ptype == models.PostType.Vote:
        vote_info = Vote.from_dict(data['vote_data'])
        vote_model = vote_info.create_model()
        vote_model.post = p
        s.add(vote_model)
    channel_id = c.story.channel_id
    browser_post_msg = {
        'type': p.post_type.name,
        'date_millis': p.posted_date.timestamp() * 1000
    }
    if p.post_type == models.PostType.Text:
        browser_post_msg['text'] = p.text
    elif p.post_type == models.PostType.Vote:
        vote_info = Vote.from_model(vote_model)
        browser_post_msg['vote_data'] = vote_info.to_dict()
    socketio.emit('new_post', browser_post_msg, room=str(channel_id))
    s.commit()
    return jsonify({ 'new_url': url_for('view_chapter', story_id=p.story.id,
                                        chapter_id=p.chapter.id) })

@app.route('/user/<int:user_id>')
def user_profile(user_id: int) -> str:
    s = db_connect()
    u = (s.query(models.User).filter(models.User.id == user_id).one_or_none())
    if u is None:
        abort(404)
    sl = (s.query(models.Story).filter(models.Story.author == u).all())
    return render_template("user_profile.html", user=u, stories=sl)

@jinja_global
def get_dark_mode() -> bool:
    return session.get('dark_mode', False)

@app.route('/settings', methods=['POST'])
def change_settings() -> str:
    dark_mode = int(request.form.get('dark_mode', 0))
    session['dark_mode'] = bool(dark_mode)
    return ''

def init_db(silent: bool = False) -> None:
    db_setup()
    assert db_engine is not None
    if not silent:
        print("Initializing DB in {}".format(db_engine.url))
    models.init_db(db_engine,
                   config.getboolean('openakun', 'use_alembic', fallback=True))

@click.command()
@click.option('--host', '-h', type=str, default=None,
              help="Hostname to bind to (default 127.0.0.1)")
@click.option('--port', '-p', type=int, default=None,
              help="Port to listen on (default 5000)")
def do_run(host: str, port: int) -> None:
    socketio.run(app, host=host, port=port)
