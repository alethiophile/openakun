#!python

from . import models

from flask import Flask, render_template, request, redirect, url_for, g, flash
from flask_login import LoginManager, login_user, current_user, logout_user

from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=['pbkdf2_sha256'],
    deprecated='auto',
)

login_mgr = LoginManager()
app = Flask('openakun')
app.config['SECRET_KEY'] = "for_testing_only"  # very temporary
login_mgr.init_app(app)

@login_mgr.user_loader
def load_user(user_id):
    s = db_connect()
    return (s.query(models.User).
            filter(models.User.id == int(user_id)).one_or_none())

def db_connect():
    if not hasattr(g, 'db_session'):
        g.db_session = models.Session()
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
