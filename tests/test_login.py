import unittest, os

test_config = {
    'openakun': {
        'database_url': 'sqlite://',
        'secret_key': 'unittest_key',
        'echo_sql': False,
        'use_alembic': False,
    },
}
os.openakun_test_config = test_config

from context import pages

class OpenakunTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(self):
        # force new empty database
        pages.app.testing = True
        # or else it calls db_setup again and re-clears
        pages.app.before_first_request_funcs.clear()
        self.client = pages.app.test_client()
        pages.init_db(silent=True)
        s = pages.Session()
        s.add(pages.create_user('admin', 'placeholder@example.com', 'test'))
        s.commit()

    def login(self, user, password):
        return self.client.post('/login',
                                data={ 'user': user, 'pass': password },
                                follow_redirects=True)

    def logout(self):
        return self.client.get('/logout', follow_redirects=True)

    @classmethod
    def tearDownClass(self):
        pass

class LoginTest(OpenakunTestCase):
    def test_login(self):
        rv = self.client.get('/')
        self.assertIn(b'Log in', rv.data)
        rv = self.login('admin', 'test')
        self.assertIn(b'Welcome, admin', rv.data)

    def test_logout(self):
        rv = self.logout()
        self.assertIn(b'Log in', rv.data)

class PostStoryTest(OpenakunTestCase):
    def test_post(self):
        rv = self.client.get('/new_story', follow_redirects=True)
        self.assertIn(b'Please log in to access this page', rv.data)
        self.login('admin', 'test')
        rv = self.client.post('/new_story',
                              data={ 'title': 'test title',
                                     'description': 'test description' },
                              follow_redirects=True)
        self.assertIn(b'<h2>test title</h2>', rv.data)
        self.assertRegex(rv.data.decode(),
                         r'<div class="description">\s*test description\s*</div>')  # noqa: E501

test_no_html = [
    ('This contains no HTML', 'This contains no HTML'),
    ('This "contains" double quotes', 'This "contains" double quotes'),
    ('This uses the symbol (x < y)', 'This uses the symbol (x &lt; y)'),
    ('Tries <script>alert("injection")</script>',
     'Tries &lt;script&gt;alert("injection")&lt;/script&gt;'),
]

test_clean_html = [
    ('This contains no HTML', 'This contains no HTML'),
    ('This "contains" double quotes', 'This "contains" double quotes'),
    ('This uses the symbol (x < y)', 'This uses the symbol (x &lt; y)'),
    ('This is <b>rich</b> text', 'This is <b>rich</b> text'),
    ('This is <strong>rich</strong> text',
     'This is <strong>rich</strong> text'),
    ('<p>This is a paragraph</p>', '<p>This is a paragraph</p>'),
    ('This has <i>italics</i>', 'This has <i>italics</i>'),
    ('This has <em>italics</em>', 'This has <em>italics</em>'),
]

class CleanHTMLTest(unittest.TestCase):
    def test_chapter_cleaner(self):
        for b, a in test_no_html + test_clean_html:
            with self.subTest(case=b):
                h = pages.ChapterHTMLText(b)
                self.assertEqual(h.dirty_html, b)
                self.assertEqual(h.clean_html, a)

# Strings like story title don't support any formatting; all HTML is escaped
# (this just happens automatically on the Jinja2 level)
class TitleXSSTest(OpenakunTestCase):
    xss_strings = { '': '' }

    def test_title_xss(self):
        pass
