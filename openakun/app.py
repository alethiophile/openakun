import quart_flask_patch

from . import models, realtime, pages, websocket, worker
from .general import (make_csrf, get_script_nonce, add_csp, csp_report,
                      db_setup, db, login_mgr, add_htmx_vary, db_close)
from .config import Config, CSPLevel

import click, signal, traceback, threading, asyncio, uvicorn
from quart import Quart, g
from quart.utils import observe_changes, MustReloadError, restart
from quart.app import _cancel_all_tasks
import sentry_sdk
from sentry_sdk import push_scope, capture_exception
from sqlalchemy import inspect

from typing import Any, NoReturn

async def close_db_session(err: Any) -> None:
    if err:
        print(err)
    if not hasattr(g, 'db_session'):
        return
    try:
        await g.db_session.close()
    except Exception:
        print("error in close_db_session()")
        traceback.print_exc()

def create_app(config: Config) -> Quart:
    app = Quart('openakun')

    if config.sentry_dsn is not None:
        sentry_sdk.init(
            dsn=config.sentry_dsn,
            send_default_pii=True,
        )

    app.config['SECRET_KEY'] = config.secret_key
    # global login_mgr
    login_mgr.init_app(app)
    login_mgr.login_view = 'questing.login'

    websocket.pubsub.set_redis_opts(config.redis_url,
                                    True, True)

    app.config['csp_report_only'] = config.csp_level == CSPLevel.Report
    # TODO merge this with the default .config attr properly
    app.config['data_obj'] = config
    app.config.update(config.merge_dict)

    app.jinja_env.add_extension('jinja2.ext.do')

    app.jinja_env.globals['models'] = models

    app.add_template_global(get_script_nonce)

    app.before_request(make_csrf)
    app.after_request(add_csp)
    app.after_request(add_htmx_vary)
    app.after_request(db_close)
    app.route('/csp_violation_report', methods=['POST'])(csp_report)

    app.register_blueprint(pages.questing)
    app.register_blueprint(websocket.rtb)

    app.teardown_appcontext(close_db_session)

    pages.htmx.init_app(app)

    return app

async def init_db(silent: bool = False) -> None:
    config = Config.get_config()
    await db_setup(config)
    assert db.db_engine is not None
    if not silent:
        print("Initializing DB in {}".format(db.db_engine.url))
    await models.init_db(db.db_engine, use_alembic=config.use_alembic)

def sigterm(signum: Any, frame: Any) -> NoReturn:
    raise KeyboardInterrupt()

def start_debug(signum: Any, frame: Any) -> None:
    print("debug signal")
    traceback.print_stack()
    print(threading.enumerate())

class DummyEvent:
    def is_set(self):
        return False

    def set(self):
        pass

@click.command()
@click.option('--host', '-h', type=str, default=None,
              help="Hostname to bind to (default 127.0.0.1)")
@click.option('--port', '-p', type=int, default=5000,
              help="Port to listen on (default 5000)")
@click.option('--debug/--no-debug', type=bool, default=False,
              help="Debug mode")
@click.option('--devel/--no-devel', type=bool, default=False,
              help="Use development server")
def do_run(host: str, port: int, debug: bool, devel: bool) -> None:
    async def runner() -> None:
        config = Config.get_config()
        await db_setup(config, force_redis=True)
        assert db.db_engine is not None
        # TODO figure out the real way to ensure DB versioning here, alembic?
        # if not inspect(db.db_engine).has_table('user_with_role'):
        #     await init_db()
        app = create_app(config)
        # signal.signal(signal.SIGTERM, sigterm)
        # signal.signal(signal.SIGINT, sigterm)
        # signal.signal(signal.SIGUSR1, start_debug)
        await realtime.repopulate_from_db()
        if not devel:
            ucfg = uvicorn.Config(app)
        tasks = []
        try:
            with websocket.pubsub:
                if debug:
                    tasks.append(
                        asyncio.create_task(
                            observe_changes(asyncio.sleep, DummyEvent())))
                tasks.append(
                    asyncio.create_task(worker.chat_save_worker()))
                tasks.append(
                    asyncio.create_task(worker.vote_close_worker()))
                # TODO figure out the reloader logic in this context
                tasks.append(
                    asyncio.create_task(
                        app.run_task(host=host, port=port, debug=debug)))
                await asyncio.gather(*tasks) 
        finally:
            print("Closing out Redis data...")
            await realtime.close_to_db()
            print("done")
    _reload = False
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(runner())
    except MustReloadError:
        _reload = True
    finally:
        try:
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    if _reload:
        restart()
