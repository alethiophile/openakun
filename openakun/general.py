from . import models

import secrets, re, sqlalchemy, importlib.resources
import redis.asyncio as redis
from quart import session, request, abort, g, current_app, url_for
from functools import wraps
from base64 import b64encode
from werkzeug import Response
from sentry_sdk import push_scope, capture_message
from .login import LoginManager
from .config import Config

from typing import Callable, Optional, Any, Iterator

class ConfigError(Exception):
    pass

login_mgr = LoginManager()

# creates an empty object, since object() can't be written to
class DbObj:
    db_engine: sqlalchemy.ext.asyncio.AsyncEngine
    Session: sqlalchemy.ext.asyncio.async_sessionmaker
    redis_conn: redis.Redis
db = DbObj()
db.db_engine, db.Session, db.redis_conn = None, None, None  # type: ignore

def decode_redis_dict(l: list | dict) -> dict[str, Any]:
    if not isinstance(l, list):
        return l
    def to_pairs(l: list) -> Iterator[tuple[str, Any]]:
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
async def db_setup(config: Config | None = None, force_redis: bool = False) -> None:
    # global db_engine, Session, redis_conn
    if config is None:
        config = current_app.config['data_obj']
    global db
    if db.db_engine is None:
        async_url = config.database_url.replace("postgresql://",
                                                "postgresql+asyncpg://", 1)
        db.db_engine = models.create_async_engine(async_url,
                                                  echo=config.echo_sql)
        db.Session = models.async_sessionmaker(bind=db.db_engine,
                                               expire_on_commit=False)
    if db.redis_conn is None:
        db.redis_conn = redis.Redis.from_url(config.redis_url)
        fl = await db.redis_conn.function_list()
        for i in fl:
            d = decode_redis_dict(i)
            if d['library_name'] == 'votes' and not force_redis:
                break
        else:
            print("adding lua function")
            lua_code = (importlib.resources.files('openakun').
                        joinpath('redisvotes.lua').read_text())
            await db.redis_conn.function_load(lua_code, replace=True)

def db_connect() -> sqlalchemy.ext.asyncio.AsyncSession:
    if not hasattr(g, 'db_session'):
        g.db_session = db.Session()
    return g.db_session

async def db_close(r: Response) -> Response:
    if hasattr(g, 'db_session'):
        await g.db_session.close()
    return r

def make_csrf(force: bool = False) -> None:
    if force or '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_urlsafe()

def csrf_check(view: Callable) -> Callable:
    @wraps(view)
    async def csrf_wrapper(*args: Any, **kwargs: Any) -> Any:
        if request.method == 'POST':
            data = await request.get_json(silent=True)
            tok = (data.get('_csrf_token', '') if data else
                   (await request.form).get('_csrf_token', ''))
            # constant-time compare operation
            if not secrets.compare_digest(tok,
                                          session['_csrf_token']):
                abort(400)
        return await view(*args, **kwargs)
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
    hval = f"script-src 'self' 'nonce-{nv}' 'unsafe-eval'"
    # TODO report even in enforcing mode
    if report_only:
        hval += f"; report-uri {url_for('csp_report')}"
    resp.headers[header_name] = hval
    return resp

def add_htmx_vary(resp: Response) -> Response:
    resp.headers['Vary'] = 'HX-Request, HX-History-Restore-Request'
    return resp

def csp_report() -> str:
    # TODO log this
    print(request.data)
    if current_app.config['using_sentry']:
        with push_scope() as scope:
            scope.set_extra('request', request.get_json())
            capture_message("CSP violation")
    return ''
