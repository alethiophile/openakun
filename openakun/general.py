from . import models

import secrets, re, redis, sqlalchemy, importlib.resources
from flask import session, request, abort, g, current_app, url_for
from functools import wraps
from base64 import b64encode
from werkzeug import Response
from sentry_sdk import push_scope, capture_message
from flask_login import LoginManager

from typing import Callable, Optional

class ConfigError(Exception):
    pass

login_mgr = LoginManager()

# creates an empty object, since object() can't be written to
class DbObj:
    db_engine: Optional[sqlalchemy.engine.Engine]
    Session: Optional[sqlalchemy.orm.sessionmaker]
    redis_conn: Optional[redis.Redis]
db = DbObj()
db.db_engine, db.Session, db.redis_conn = None, None, None

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

def decode_redis_dict(l):
    if not isinstance(l, list):
        return l
    def to_pairs(l):
        i = iter(l)
        while True:
            try:
                a = next(i)
                b = next(i)
            except StopIteration:
                break
            try:
                b = b.decode()
            except AttributeError:
                pass
            yield (a.decode(), b)
    return dict(to_pairs(l))

# TODO make this per-request?
def db_setup(config=None, force_redis=False) -> None:
    # global db_engine, Session, redis_conn
    if config is None:
        config = current_app.config_data
    global db
    if db.db_engine is None:
        db.db_engine = models.create_engine(config['openakun']['database_url'],
                                            echo=config.getboolean('openakun',
                                                                   'echo_sql'))
        db.Session = models.sessionmaker(bind=db.db_engine)
    if db.redis_conn is None:
        redis_info = parse_redis_url(config['openakun']['redis_url'])
        db.redis_conn = redis.StrictRedis(**redis_info)
        fl = db.redis_conn.function_list()
        for i in fl:
            d = decode_redis_dict(i)
            if d['library_name'] == 'votes' and not force_redis:
                break
        else:
            print("adding lua function")
            lua_code = (importlib.resources.files('openakun').
                        joinpath('redisvotes.lua').read_text())
            db.redis_conn.function_load(lua_code, replace=True)

def db_connect() -> sqlalchemy.orm.Session:
    if not hasattr(g, 'db_session'):
        g.db_session = db.Session()
    return g.db_session

def make_csrf(force: bool = False) -> None:
    if force or '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe()

def csrf_check(view: Callable) -> Callable:
    @wraps(view)
    def csrf_wrapper(*args, **kwargs):
        if request.method == 'POST':
            data = request.get_json(silent=True)
            tok = (data.get('_csrf_token', '') if data else
                   request.form.get('_csrf_token', ''))
            # constant-time compare operation
            if not secrets.compare_digest(tok,
                                          session['_csrf_token']):
                abort(400)
        return view(*args, **kwargs)
    return csrf_wrapper

def get_script_nonce() -> str:
    if not hasattr(g, 'script_nonce'):
        # this has to be real base64, per the spec, not urlencoded; thus
        # token_urlsafe won't work
        g.script_nonce = b64encode(secrets.token_bytes()).decode()
    return g.script_nonce

def add_csp(resp: Response) -> Response:
    nv = get_script_nonce()
    report_only = current_app.config['csp_report_only']
    header_name = ('Content-Security-Policy-Report-Only' if report_only else
                   'Content-Security-Policy')
    hval = f"script-src 'nonce-{nv}' 'unsafe-eval'"
    # TODO report even in enforcing mode
    if report_only:
        hval += f"; report-uri {url_for('csp_report')}"
    resp.headers[header_name] = hval
    return resp

def csp_report() -> str:
    # TODO log this
    print(request.data)
    if current_app.config['using_sentry']:
        with push_scope() as scope:
            scope.set_extra('request', request.get_json())
            capture_message("CSP violation")
    return ''
