#!python

from . import models

from flask import (Flask, render_template, request, redirect, url_for, g,
                   flash, abort)
from flask_login import (LoginManager, login_user, current_user, logout_user,
                         login_required)

from passlib.context import CryptContext

import datetime, configparser, bleach, os

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

if ('secret_key' not in config['openakun'] or
    len(config['openakun']['secret_key']) == 0):  # noqa: E129
    raise RuntimeError("Secret key not provided")

app.config['SECRET_KEY'] = config['openakun']['secret_key']
login_mgr.init_app(app)
login_mgr.login_view = 'login'

db_engine = models.create_engine(config['openakun']['database_url'],
                                 echo=config.getboolean('openakun',
                                                        'echo_sql'))
Session = models.sessionmaker(bind=db_engine)

@login_mgr.user_loader
def load_user(user_id):
    s = db_connect()
    return (s.query(models.User).
            filter(models.User.id == int(user_id)).one_or_none())

def db_connect():
    if not hasattr(g, 'db_session'):
        g.db_session = Session()
    return g.db_session

@app.route('/')
def main():
    return render_template("main.html", user=current_user)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        s = db_connect()
        user = (s.query(models.User).
                filter(models.User.name == request.form['user']).one_or_none())
        if user is not None and pwd_context.verify(request.form['pass'],
                                                   user.password_hash):
            login_user(user)
            next_url = request.form.get('next', url_for('main'))
            return redirect(next_url)
        else:
            flash("Login failed")
            return redirect(url_for('login'))
    else:
        return render_template("login.html", user=current_user)

@app.route('/logout')
def logout():
    if not current_user.is_anonymous:
        logout_user()
    return redirect(url_for('main'))

def create_user(name, email, password):
    return models.User(
        name=name,
        email=email,
        password_hash=pwd_context.hash(password),
        joined_date=datetime.datetime.now()
    )

def add_user(name, email, password):
    s = db_connect()
    u = create_user(name, email, password)
    s.add(u)
    s.commit()
    return u

@app.route('/signup', methods=['GET', 'POST'])
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
        add_user(request.form['user'], request.form['email'],
                 request.form['pass1'])
        flash("Registration successful. Now log in.")
        return redirect(url_for('login'))
    else:
        return render_template("signup.html", user=current_user)

class BadHTMLError(Exception):
    def __init__(self, *args, good_html, bad_html, **kwargs):
        self.good_html = good_html
        self.bad_html = bad_html
        super().__init__(*args, **kwargs)

allowed_tags = ['a', 'b', 'em', 'i', 'li', 'ol', 's', 'strong', 'strike', 'ul']

def allowed_attributes(tag, name, value):
    if tag != 'a':
        return False
    if name not in ['class', 'data-achieve']:
        return False
    if name == 'class' and value != 'achieve-link':
        return False
    return True

def clean_html(html_in):
    html_out = bleach.clean(html_in,
                            tags=allowed_tags,
                            attributes=allowed_attributes)
    if html_in != html_out:
        raise BadHTMLError(good_html=html_out, bad_html=html_in)
    return html_out

@app.route('/new_story', methods=['GET', 'POST'])
@login_required
def post_story():
    if request.method == 'POST':
        s = db_connect()
        try:
            desc_html = clean_html(request.form['description'])
        except BadHTMLError:
            abort(400)
        ns = models.Story(title=request.form['title'], description=desc_html)
        ns.author = current_user
        s.add(ns)
        s.commit()
        return redirect(url_for('view_story', story_id=ns.id))
    else:
        return render_template("post_story.html", user=current_user)

@app.route('/story/<int:story_id>')
def view_story(story_id):
    s = db_connect()
    story = s.query(models.Story).filter(models.Story.id == story_id).one()
    return render_template("view_story.html", user=current_user, story=story)

def init_db():
    print("Initializing DB in {}".format(db_engine.url))
    models.init_db(db_engine)
