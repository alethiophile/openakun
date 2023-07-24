from . import models, realtime, pages
from .general import (make_csrf, get_script_nonce, add_csp, csp_report,
                      ConfigError, db_setup, db, login_mgr)

import configparser, click, os
from flask import Flask, request
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

        @realtime.socketio.on_error_default
        def sentry_report_socketio(e):
            print(e)
            with push_scope() as scope:
                scope.set_extra('event', request.event)
                capture_exception()

    if ('secret_key' not in config['openakun'] or
        len(config['openakun']['secret_key']) == 0):  # noqa: E129
        raise ConfigError("Secret key not provided")

    app.config['SECRET_KEY'] = config['openakun']['secret_key']
    global login_mgr
    login_mgr.init_app(app)
    login_mgr.login_view = 'login'
    realtime.socketio.init_app(app,
                               logger=app.config['DEBUG'],
                               engineio_logger=app.config['DEBUG'])

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
    app.route('/csp_violation_report', methods=['POST'])(csp_report)

    app.register_blueprint(pages.questing)

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
    db_setup(config)
    assert db.db_engine is not None
    # TODO figure out the real way to ensure DB versioning here, alembic?
    if not inspect(db.db_engine).has_table('user_with_role'):
        init_db()
    app = create_app(config)
    realtime.socketio.run(app, host=host, port=port, debug=debug,
                          allow_unsafe_werkzeug=devel)
