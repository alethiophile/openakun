import pytest, re

# class OpenakunTestCase(unittest.TestCase):
#     @classmethod
#     def setUpClass(self):
#         # force new empty database
#         pages.app.testing = True
#         # or else it calls db_setup again and re-clears
#         pages.app.before_first_request_funcs.clear()
#         self.client = pages.app.test_client()
#         pages.init_db(silent=True)
#         s = pages.Session()
#         s.add(pages.create_user('admin', 'placeholder@example.com', 'test'))
#         s.commit()

#     def get_csrf(self, url='/'):
#         rv = self.client.get(url)
#         csrf = re.search(r'name="_csrf_token" value="([^"]+)"',
#                          rv.data.decode()).group(1)
#         return csrf

#     def login(self, user, password):
#         csrf = self.get_csrf('/login')
#         return self.client.post('/login',
#                                 data={ 'user': user, 'pass': password,
#                                        '_csrf_token': csrf },
#                                 follow_redirects=True)

#     def logout(self):
#         csrf = self.get_csrf()
#         return self.client.post('/logout', data={ '_csrf_token': csrf},
#                                 follow_redirects=True)

#     @classmethod
#     def tearDownClass(self):
#         pass

async def test_main(openakun_app):
    client = openakun_app.test_client()
    resp = await client.get('/')
    assert resp.status_code == 200

async def test_login(openakun_app):
    client = openakun_app.test_client()
    resp = await client.get('/')
    assert resp.status_code == 200

    assert re.search(r"<a[^>]*>Log in</a>", await resp.get_data(True))

    lresp = await client.get('/login')
    csrf_token = re.search(r'name="_csrf_token" value="([^"]+)"',
                           await lresp.get_data(True)).group(1)
    data = {
        'user': 'admin', 'pass': 'password',
        '_csrf_token': csrf_token
    }
    lresp = await client.post('/login', form=data)
    assert lresp.status_code == 302

    lresp = await client.get('/')
    assert lresp.status_code == 200
    assert re.search(r"<a[^>]*>Log out</a>", await lresp.get_data(True))

# class LoginTest(OpenakunTestCase):
#     def test_login(self):
#         rv = self.client.get('/')
#         self.assertIn(b'Log in', rv.data)
#         rv = self.login('admin', 'test')
#         self.assertIn(b'Welcome, admin', rv.data)

#     def test_logout(self):
#         rv = self.logout()
#         self.assertIn(b'Log in', rv.data)

# class PostStoryTest(OpenakunTestCase):
#     def test_post(self):
#         rv = self.client.get('/new_story', follow_redirects=True)
#         self.assertIn(b'Please log in to access this page', rv.data)
#         self.login('admin', 'test')
#         csrf = self.get_csrf()
#         rv = self.client.post('/new_story',
#                               data={ 'title': 'test title',
#                                      'description': 'test description',
#                                      '_csrf_token': csrf },
#                               follow_redirects=True)
#         self.assertIn(b'<h2>test title</h2>', rv.data)
#         self.assertRegex(rv.data.decode(),
#                          r'<div class="description">\s*test description\s*</div>')  # noqa: E501

# test_no_html = [
#     ('This contains no HTML', 'This contains no HTML'),
#     ('This "contains" double quotes', 'This "contains" double quotes'),
#     ('This uses the symbol (x < y)', 'This uses the symbol (x &lt; y)'),
#     ('Tries <script>alert("injection")</script>',
#      'Tries &lt;script&gt;alert("injection")&lt;/script&gt;'),
# ]

# test_clean_html = [
#     ('This contains no HTML', 'This contains no HTML'),
#     ('This "contains" double quotes', 'This "contains" double quotes'),
#     ('This uses the symbol (x < y)', 'This uses the symbol (x &lt; y)'),
#     ('This is <b>rich</b> text', 'This is <b>rich</b> text'),
#     ('This is <strong>rich</strong> text',
#      'This is <strong>rich</strong> text'),
#     ('<p>This is a paragraph</p>', '<p>This is a paragraph</p>'),
#     ('This has <i>italics</i>', 'This has <i>italics</i>'),
#     ('This has <em>italics</em>', 'This has <em>italics</em>'),
# ]

# class CleanHTMLTest(unittest.TestCase):
#     def test_chapter_cleaner(self):
#         for b, a in test_no_html + test_clean_html:
#             with self.subTest(case=b):
#                 h = pages.ChapterHTMLText(b)
#                 self.assertEqual(h.dirty_html, b)
#                 self.assertEqual(h.clean_html, a)

# # Strings like story title don't support any formatting; all HTML is escaped
# # (this just happens automatically on the Jinja2 level)
# class TitleXSSTest(OpenakunTestCase):
#     xss_strings = { '': '' }

#     def test_title_xss(self):
#         pass
