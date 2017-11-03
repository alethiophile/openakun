#!python

from . import models

from flask import (Flask, render_template, request, redirect, url_for, g,
                   flash, abort, jsonify, session)
from flask_login import (LoginManager, login_user, current_user, logout_user,
                         login_required)
from jinja2 import Markup
from flask_socketio import SocketIO
from raven.contrib.flask import Sentry

import itsdangerous, redis
from passlib.context import CryptContext

import datetime, configparser, bleach, os, secrets, re
from functools import wraps

pwd_context = CryptContext(
    schemes=['pbkdf2_sha256'],
    deprecated='auto',
)

config = configparser.ConfigParser()
if os.environ.get('OPENAKUN_TESTING') == '1':
    config.read_dict(os.openakun_test_config)  # this is a terrible hack
else:
    rv = config.read('openakun.cfg')
    if len(rv) == 0:
        raise RuntimeError("Couldn't find config file")

login_mgr = LoginManager()
app = Flask('openakun')

class ConfigError(Exception):
    pass

sentry = None
if 'sentry_dsn' in config['openakun']:
    sentry = Sentry(app, dsn=config['openakun']['sentry_dsn'])

if ('secret_key' not in config['openakun'] or
    len(config['openakun']['secret_key']) == 0):  # noqa: E129
    raise ConfigError("Secret key not provided")

app.config['SECRET_KEY'] = config['openakun']['secret_key']
login_mgr.init_app(app)
login_mgr.login_view = 'login'
socketio = SocketIO(app)

app.jinja_env.add_extension('jinja2.ext.do')

from . import realtime

def jinja_global(f):
    app.jinja_env.globals[f.__name__] = f
    return f

@jinja_global
def include_raw(filename):
    return Markup(app.jinja_loader.get_source(app.jinja_env, filename)[0])

db_engine = None
Session = None
redis_conn = None

redis_url_re = re.compile(r"^redis://(?P<hostname>[a-zA-Z1-9.-]+)?" +
                          "(:(?P<port>\d+))?" +
                          "(/(?P<db>\d+))?$")

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
def db_setup():
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
def make_csrf(force=False):
    if force or '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe()

def csrf_check(view):
    @wraps(view)
    def csrf_wrapper(*args, **kwargs):
        if request.method == 'POST':
            # constant-time compare operation
            if not secrets.compare_digest(request.form.get('_csrf_token', ''),
                                          session['_csrf_token']):
                abort(400)
        return view(*args, **kwargs)
    return csrf_wrapper

@login_mgr.user_loader
def load_user(user_id):
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
    return render_template("main.html", user=current_user, stories=stories)

@app.route('/login', methods=['GET', 'POST'])
@csrf_check
def login():
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
        return render_template("login.html", user=current_user)

@app.route('/logout', methods=['POST'])
@csrf_check
def logout():
    if not current_user.is_anonymous:
        logout_user()
        make_csrf(force=True)
    return redirect(url_for('main'))

def create_user(name, email, password):
    return models.User(
        name=name,
        email=email,
        password_hash=pwd_context.hash(password),
        joined_date=datetime.datetime.utcnow()
    )

def add_user(name, email, password):
    s = db_connect()
    u = create_user(name, email, password)
    s.add(u)
    s.commit()
    return u

@app.route('/signup', methods=['GET', 'POST'])
@csrf_check
def register():
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
        return render_template("signup.html", user=current_user)

class BadHTMLError(Exception):
    def __init__(self, *args, good_html, bad_html, **kwargs):
        self.good_html = good_html
        self.bad_html = bad_html
        super().__init__(*args, **kwargs)

class HTMLText(object):
    def __init__(self, html_data):
        self.dirty_html = html_data
        self.clean_html = bleach.clean(html_data,
                                       tags=self.allowed_tags,
                                       attributes=self.allowed_attributes)

    def __str__(self):
        return self.clean_html

class ChapterHTMLText(HTMLText):
    allowed_tags = ['a', 'b', 'br', 'em', 'i', 'li', 'ol', 'p', 's', 'strong',
                    'strike', 'ul']

    def allowed_attributes(self, tag, name, value):
        if tag == 'a':
            if name == 'data-achieve': return True
            if name == 'class' and value == 'achieve-link': return True
        return False

def clean_html(html_in):
    html = ChapterHTMLText(html_in)
    if html.clean_html != html.dirty_html:
        raise BadHTMLError(good_html=html.clean_html, bad_html=html.dirty_html)
    return html.clean_html

def add_story(title, desc, author):
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
def post_story():
    if request.method == 'POST':
        try:
            ns = add_story(request.form['title'], request.form['description'],
                           current_user)
        except BadHTMLError:
            if app.config['DEBUG']:
                raise
            else:
                abort(400)
        return redirect(url_for('view_story', story_id=ns.id))
    else:
        return render_template("post_story.html", user=current_user)

@app.route('/story/<int:story_id>')
def view_story(story_id):
    s = db_connect()
    story = s.query(models.Story).filter(models.Story.id == story_id).one()
    return redirect(url_for('view_chapter', story_id=story.id,
                            chapter_id=story.chapters[0].id))

@jinja_global
def prepare_post(p):
    p.rendered_date = (p.posted_date.astimezone(datetime.timezone.utc).
                       strftime("%b %d, %Y %I:%M %p UTC"))
    p.date_millis = (p.posted_date.timestamp() * 1000)

@app.route('/story/<int:story_id>/<int:chapter_id>')
def view_chapter(story_id, chapter_id):
    s = db_connect()
    chapter = (s.query(models.Chapter).
               filter(models.Chapter.id == chapter_id,
                      models.Chapter.story_id == story_id).
               one_or_none())
    if chapter is None:
        abort(404)
    return render_template("view_chapter.html", user=current_user,
                           chapter=chapter, server=True)

def create_post(chapter_id, text, order_idx=None):
    s = db_connect()
    c = s.query(models.Chapter).filter(models.Chapter.id == chapter_id).one()
    text_clean = ChapterHTMLText(text)
    # if no explicit order given, it's the last in the current
    # chapter, plus 10
    if order_idx is None:
        order_idx = (s.query(models.func.max(models.Post.order_idx).
                             label('max')).one().max)
        if order_idx is None:
            order_idx = 0
        else:
            order_idx += 10
    np = models.Post(
        text=text_clean.clean_html,
        posted_date=datetime.datetime.now(),
        chapter=c, story=c.story, order_idx=order_idx)
    s.add(np)
    s.commit()
    return np

@app.route('/new_post', methods=['POST'])
@csrf_check
def new_post():
    s = db_connect()
    c = (s.query(models.Chapter).
         filter(models.Chapter.id == request.form['chapter_id']).one())
    if current_user != c.story.author:
        abort(403)
    p = create_post(request.form['chapter_id'], request.form['post_text'])
    return jsonify({ 'new_url': url_for('view_chapter', story_id=p.story.id,
                                        chapter_id=p.chapter.id) })

def init_db(silent=False):
    db_setup()
    if not silent:
        print("Initializing DB in {}".format(db_engine.url))
    models.init_db(db_engine,
                   config.getboolean('openakun', 'use_alembic', fallback=True))
