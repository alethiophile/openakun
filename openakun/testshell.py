# Commands to set up a test environment, kind of. Paste into a Python shell to
# set things up for experimentation.
import os
os.environ['OPENAKUN_TESTING'] = '1'
test_config = {
    'openakun': {
        'database_url': 'sqlite://',
        'secret_key': 'unittest_key',
        'echo_sql': False,
        'use_alembic': False,
    },
}
os.openakun_test_config = test_config
import openakun.pages as pages
ctx = pages.app.app_context()
ctx.__enter__()
pages.db_setup()
pages.init_db()
u = pages.add_user('admin', 'test@somewhere', 'test')
s = pages.add_story('title', 'desc', u)
ds = pages.db_connect()
c = pages.models.Chapter(story=s, order_idx=10)
ds.add(c)
ds.commit()
