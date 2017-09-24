#!python

from . import models

from flask import Flask, render_template, request, redirect, url_for, g, flash
from flask_login import LoginManager, login_user, current_user, logout_user

from passlib.context import CryptContext

import datetime, configparser

pwd_context = CryptContext(
    schemes=['pbkdf2_sha256'],
    deprecated='auto',
)

config = configparser.ConfigParser()
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
            return redirect(url_for('main'))
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
        u = create_user(request.form['user'], request.form['email'],
                        request.form['pass1'])
        s.add(u)
        s.commit()
        flash("Registration successful. Now log in.")
        return redirect(url_for('login'))
    else:
        return render_template("signup.html", user=current_user)

def init_db():
    models.init_db(db_engine)
