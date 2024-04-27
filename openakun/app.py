from . import models, realtime, pages, websocket
from .general import (make_csrf, get_script_nonce, add_csp, csp_report,
                      ConfigError, db_setup, db, login_mgr, add_htmx_vary)

import configparser, click, os, signal, traceback
from flask import Flask, g
from werkzeug.middleware.proxy_fix import ProxyFix
import sentry_sdk
from sentry_sdk import push_scope, capture_exception
from sentry_sdk.integrations.flask import FlaskIntegration
from sqlalchemy import inspect

def get_config(config_fn: str):
    config = configparser.ConfigParser()
    rv = config.read(config_fn)
    if len(rv) == 0:
        raise RuntimeError(f"Couldn't find config file {config_fn}")

    return config

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

def create_app(config):
    app = Flask('openakun')

    app.config['using_sentry'] = False
    if 'sentry_dsn' in config['openakun']:
        app.config['using_sentry'] = True
        sentry_sdk.init(
            dsn=config['openakun']['sentry_dsn'],
            send_default_pii=True,
            integrations=[FlaskIntegration()]
        )

    if ('secret_key' not in config['openakun'] or
        len(config['openakun']['secret_key']) == 0):  # noqa: E129
        raise ConfigError("Secret key not provided")

    app.config['SECRET_KEY'] = config['openakun']['secret_key']
    global login_mgr
    login_mgr.init_app(app)
    login_mgr.login_view = 'login'
    websocket.sock.init_app(app)

    # to make the proxy_fix apply to the socketio as well, this has to be done
    # after the socketio is connected to the app
    if config.getboolean('openakun', 'proxy_fix', fallback=False):
        print("adding ProxyFix")
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app.config['csp_report_only'] = config['openakun']['csp_level'] == 'report'
    # TODO merge this with the default .config attr properly
    app.config_data = config

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
    config_fn = os.environ.get("OPENAKUN_CONFIG", 'openakun.cfg')
    config = get_config(config_fn)
    db_setup(config)
    assert db.db_engine is not None
    if not silent:
        print("Initializing DB in {}".format(db.db_engine.url))
    models.init_db(db.db_engine,
                   config.getboolean('openakun', 'use_alembic', fallback=True))

def sigterm(signum, frame):
    raise KeyboardInterrupt()

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
    config_fn = os.environ.get("OPENAKUN_CONFIG", 'openakun.cfg')
    config = get_config(config_fn)
    db_setup(config, force_redis=True)
    assert db.db_engine is not None
    # TODO figure out the real way to ensure DB versioning here, alembic?
    if not inspect(db.db_engine).has_table('user_with_role'):
        init_db()
    app = create_app(config)
    signal.signal(signal.SIGTERM, sigterm)
    signal.signal(signal.SIGINT, sigterm)
    realtime.repopulate_from_db()
    try:
        app.run(host=host, port=port, debug=debug)
    finally:
        print("Closing out Redis data...")
        realtime.close_to_db()
