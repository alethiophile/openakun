from . import models, realtime, pages, websocket
from .general import (make_csrf, get_script_nonce, add_csp, csp_report,
                      ConfigError, db_setup, db, login_mgr, add_htmx_vary)
from .config import Config, CSPLevel

import click, os, signal, traceback, threading
from flask import Flask, g
from werkzeug.middleware.proxy_fix import ProxyFix
import sentry_sdk
from sentry_sdk import push_scope, capture_exception
from sentry_sdk.integrations.flask import FlaskIntegration
from sqlalchemy import inspect

def close_db_session(err):
    if err:
        print(err)
    if not hasattr(g, 'db_session'):
        return
    try:
        g.db_session.close()
    except Exception:
        print("error in close_db_session()")
        traceback.print_exc()

    app = Flask('openakun')
def create_app(config: Config):

    if config.sentry_dsn is not None:
        sentry_sdk.init(
            dsn=config.sentry_dsn,
            send_default_pii=True,
            integrations=[FlaskIntegration()]
        )

    app.config['SECRET_KEY'] = config.secret_key
    global login_mgr
    login_mgr.init_app(app)
    login_mgr.login_view = 'login'
    websocket.sock.init_app(app)

    websocket.pubsub.set_redis_opts(config.redis_url,
                                    True, True)

    # to make the proxy_fix apply to the socketio as well, this has to be done
    # after the socketio is connected to the app
    if config.proxy_fix:
        print("adding ProxyFix")
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app.config['csp_report_only'] = config.csp_level == CSPLevel.Report
    # TODO merge this with the default .config attr properly
    app.config['data_obj'] = config

    app.jinja_env.add_extension('jinja2.ext.do')

    app.jinja_env.globals['models'] = models

    app.add_template_global(get_script_nonce)

    app.before_request(make_csrf)
    app.after_request(add_csp)
    app.after_request(add_htmx_vary)
    app.route('/csp_violation_report', methods=['POST'])(csp_report)

    app.register_blueprint(pages.questing)

    app.teardown_appcontext(close_db_session)

    pages.htmx.init_app(app)

    return app

def init_db(silent: bool = False) -> None:
    config = Config.get_config()
    db_setup(config)
    assert db.db_engine is not None
    if not silent:
        print("Initializing DB in {}".format(db.db_engine.url))
    models.init_db(db.db_engine,
                   config.use_alembic)

def sigterm(signum, frame):
    raise KeyboardInterrupt()

def start_debug(signum, frame):
    print("debug signal")
    traceback.print_stack()
    print(threading.enumerate())

@click.command()
@click.option('--host', '-h', type=str, default=None,
              help="Hostname to bind to (default 127.0.0.1)")
@click.option('--port', '-p', type=int, default=None,
              help="Port to listen on (default 5000)")
@click.option('--debug/--no-debug', type=bool, default=False,
              help="Debug mode")
@click.option('--devel/--no-devel', type=bool, default=False,
              help="Use development server")
def do_run(host: str, port: int, debug: bool, devel: bool) -> None:
    config = Config.get_config()
    db_setup(config, force_redis=True)
    assert db.db_engine is not None
    # TODO figure out the real way to ensure DB versioning here, alembic?
    if not inspect(db.db_engine).has_table('user_with_role'):
        init_db()
    app = create_app(config)
    signal.signal(signal.SIGTERM, sigterm)
    signal.signal(signal.SIGINT, sigterm)
    signal.signal(signal.SIGUSR1, start_debug)
    realtime.repopulate_from_db()
    try:
        app.run(host=host, port=port, debug=debug)
    finally:
        print("Closing out Redis data...")
        realtime.close_to_db()
        print("done")
