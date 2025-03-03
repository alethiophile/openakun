#!python3

from __future__ import annotations
from quart import (g, session, current_app, Response, request, redirect,
                   url_for, abort, Quart)
from itsdangerous import Signer
import inspect, base64
from datetime import datetime, timedelta
from functools import wraps

from typing import Callable, Awaitable, cast, TypeVar, Any

UserType = TypeVar('UserType')

# A hacky reimplementation of flask-login that allows using async functions for
# the user loader.

def is_async(f: Callable) -> bool:
    return inspect.iscoroutinefunction(f) or (
        hasattr(f, '__call__') and inspect.iscoroutinefunction(f.__call__)
    )

def login_user(user: UserType, remember_me: bool = False) -> None:
    # figuring out the actual right way to do this is too much of a pain
    uid = user.id  # type: ignore

    remember_dl = datetime.now() + timedelta(weeks=2) if remember_me else None
    session['_user_id'] = uid
    session['_remember_login'] = remember_dl

def logout_user() -> None:
    session.pop('_user_id', None)
    session.pop('_remember_login', None)

# this only works on async functions for the moment
def login_required(f: Callable) -> Callable:
    @wraps(f)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if getattr(g, 'current_user', None) is None:
            mgr: LoginManager = current_app.login_manager  # type: ignore
            login_view = mgr.login_view
            if isinstance(login_view, str):
                return redirect(url_for(login_view, next=request.path))
            else:
                abort(403)
        return await f(*args, **kwargs)
    return wrapper

def _make_signed_cookie(key: str, val: str) -> str:
    # hacky salt
    sn = Signer(key + ":cookie")
    iv = sn.sign(val)
    return base64.b64encode(iv).decode()

# returns None on validation failure
def _check_signed_cookie(key: str, ck: str) -> str | None:
    try:
        dec = base64.b64decode(ck, validate=True)
    except Exception:
        return None
    sn = Signer(key + ":cookie")
    try:
        res = sn.unsign(dec).decode()
    except Exception:
        return None
    return res

def _set_remember_cookie(response: Response) -> Response:
    remember_dl = session.get('_remember_login')
    uid = session.get('_user_id') if remember_dl else None

    if uid is not None:
        cval = _make_signed_cookie(current_app.config['SECRET_KEY'], str(uid))
        response.set_cookie(
            "remember_user", cval, expires=remember_dl,
            secure=current_app.config.get('SESSION_COOKIE_SECURE', True),
            httponly=True
        )
    else:
        response.set_cookie("remember_user", "", expires=0)

    return response

class LoginManager:
    def __init__(self) -> None:
        self.user_callback: \
            Callable[[str], UserType | Awaitable[UserType]] | None = None
        self.login_view: str | None = None

    # used as a decorator
    def user_loader(self, cb: Callable) -> Callable:
        self.user_callback = cb
        return self.user_callback

    def init_app(self, app: Quart) -> None:
        # self.app = app
        app.login_manager = self  # type: ignore

        # unlike flask-login, which calls the user loader only lazily when
        # current_user is evaluated, we do it on every request; this is so that
        # we can reliably get the current user in contexts that don't allow
        # await, like templates
        app.before_request(self.before_request)
        # note that the login and logout functions won't work from websocket
        # code, since they require changing the session
        app.before_websocket(self.before_request)

        app.after_request(_set_remember_cookie)

    async def _load_user(self) -> UserType | None:
        if self.user_callback is None:
            raise RuntimeError("missing user_loader in LoginManager")

        if hasattr(g, 'current_user'):
            return g.current_user

        uid = session.get('_user_id')

        if uid is None:
            cval = request.cookies.get('remember_user')
            if cval is not None:
                uid = _check_signed_cookie(current_app.config['SECRET_KEY'],
                                           cval)

        if uid is None:
            g.current_user = None
            return None

        if is_async(self.user_callback):
            ucb = cast(Callable[[str], Awaitable[UserType]],
                       self.user_callback)
            user = await ucb(uid)
        else:
            uscb = cast(Callable[[str], UserType],
                        self.user_callback)
            user = uscb(uid)

        g.current_user = user
        return user

    async def before_request(self) -> None:
        await self._load_user()
